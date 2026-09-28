// RLVid: BakkesMod plugin driven over rcon by the Python recorder.
//
// Commands (add them to bakkesmod/data/rcon_commands.cfg):
//   rlvid_play "<path to .replay>" "<target>"
//       Load and play a replay. <target> is a camera focus id such as
//       "Player_Epic|<id>|0", or a player name. From the first frame the
//       camera is locked in Player View on that player, the replay controls
//       HUD is hidden and player name tags are shown. This is re-checked every
//       frame, so it holds through kickoffs and goals. Optional third
//       argument N > 0: use the Director camera during each kickoff until N
//       seconds after the ball is first touched.
//       Optional fourth argument "hold": pause on the first frame (camera
//       already set) until rlvid_release, so recording can start first.
//   rlvid_release
//       Resume a replay held by rlvid_play ... hold.
//   rlvid_info
//       Log camera/player diagnostics to the BakkesMod console.
//
// Every 0.5 s the plugin writes bakkesmod/data/rlvid_status.json so the
// Python side can tell when the replay is playing and when it has ended.

#include <bakkesmod/plugin/bakkesmodplugin.h>
#include <bakkesmod/wrappers/includes.h>
#include <bakkesmod/wrappers/GameObject/ReplayManagerWrapper.h>

#include <cctype>
#include <cmath>
#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <sstream>

class RLVid : public BakkesMod::Plugin::BakkesModPlugin
{
public:
    void onLoad() override;
    void onUnload() override;

private:
    void Play(const std::string& path, const std::string& target, float kickoffSeconds, bool hold);
    void Enforce();
    void UpdateKickoff();
    void LogCameraChange(ReplayViewerDataWrapper& viewer);
    std::string ResolveTarget();
    void Info();
    void Release();
    void Tick();
    void WriteStatus();
    ReplayViewerDataWrapper Viewer();
    std::string FocusedName();
    std::filesystem::path StatusPath();

    std::string target_;         // what rlvid_play asked for (id or name)
    std::string focusId_;        // resolved "Player_<Platform>|<id>|0"
    bool locked_ = false;        // camera confirmed on target at least once
    bool seenReplay_ = false;    // replay started since the last rlvid_play
    bool awaitingLoad_ = false;  // rlvid_play was called inside a replay; ignore it until it unloads
    int corrections_ = 0;
    bool holdAtStart_ = false;   // pause on the first frame until rlvid_release
    float kickoffSeconds_ = 0;   // >0: Director camera on kickoffs until this long after the first touch
    bool inKickoff_ = false;
    bool wasKickoff_ = false;
    int releaseFrame_ = -1;
    std::string lastLoggedMode_, lastLoggedName_;        // times the view had to be put back after locking
    std::string lastEvent_ = "loaded";
    std::chrono::steady_clock::time_point lastWrite_{};
};

BAKKESMOD_PLUGIN(RLVid, "RLVid replay recorder helper", "1.1", PLUGINTYPE_REPLAY)

static const char* kTickEvent = "Function Engine.GameViewportClient.Tick";
static const char* kCameraMode = "PlayerView";
static const char* kDirectorMode = "Camera_Director";

// Lowercase ASCII letters and digits only, so "µµµµµZen" matches "Zen".
static std::string NameKey(const std::string& s)
{
    std::string out;
    for (unsigned char c : s)
        if (c < 0x80 && std::isalnum(c)) out += (char)std::tolower(c);
    return out;
}

static std::string JsonEscape(const std::string& s)
{
    std::string out;
    for (char c : s) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if ((unsigned char)c < 0x20) out += ' ';
        else out += c;
    }
    return out;
}

void RLVid::onLoad()
{
    cvarManager->registerNotifier("rlvid_play", [this](std::vector<std::string> args) {
        float kickoff = 0;
        if (args.size() > 3) {
            try { kickoff = std::stof(args[3]); } catch (...) {}
        }
        bool hold = args.size() > 4 && args[4] == "hold";
        Play(args.size() > 1 ? args[1] : "", args.size() > 2 ? args[2] : "", kickoff, hold);
    }, "Play a replay locked on a player: rlvid_play \"<path>\" \"<focus id or name>\" [kickoff director seconds] [hold]", PERMISSION_ALL);

    cvarManager->registerNotifier("rlvid_release", [this](std::vector<std::string>) {
        Release();
    }, "Resume a replay held on its first frame by rlvid_play ... hold", PERMISSION_ALL);

    cvarManager->registerNotifier("rlvid_info", [this](std::vector<std::string>) {
        Info();
    }, "Log replay camera/player diagnostics", PERMISSION_ALL);

    // A hooked event (not a self-rescheduling SetTimeout) so BakkesMod can
    // remove it cleanly on "plugin unload"; pending timeouts crash the game.
    gameWrapper->HookEvent(kTickEvent, [this](std::string) { Tick(); });
}

void RLVid::onUnload()
{
    gameWrapper->UnhookEvent(kTickEvent);
}

void RLVid::Tick()
{
    Enforce();
    auto now = std::chrono::steady_clock::now();
    if (now - lastWrite_ < std::chrono::milliseconds(500)) return;
    lastWrite_ = now;
    WriteStatus();
}

std::filesystem::path RLVid::StatusPath()
{
    return gameWrapper->GetDataFolder() / "rlvid_status.json";
}

void RLVid::Play(const std::string& path, const std::string& target, float kickoffSeconds, bool hold)
{
    if (path.empty()) { lastEvent_ = "play_error: missing path"; return; }
    if (!std::filesystem::exists(path)) {
        lastEvent_ = "play_error: file not found";
        cvarManager->log("rlvid_play: file not found: " + path);
        return;
    }
    auto rm = gameWrapper->GetReplayManagerWrapper();
    if (!rm) { lastEvent_ = "play_error: no replay manager"; return; }
    target_ = target;
    focusId_.clear();
    locked_ = false;
    seenReplay_ = false;
    awaitingLoad_ = gameWrapper->IsInReplay();
    corrections_ = 0;
    kickoffSeconds_ = kickoffSeconds;
    holdAtStart_ = hold;
    inKickoff_ = wasKickoff_ = false;
    releaseFrame_ = -1;
    lastEvent_ = "play_requested";
    cvarManager->log("rlvid_play: " + path + " target " + target);
    rm.PlayReplayFile(path);
}

ReplayViewerDataWrapper RLVid::Viewer()
{
    auto pc = gameWrapper->GetPlayerController();
    if (!pc) return ReplayViewerDataWrapper(0);
    auto hud = pc.GetSpectatorHud();
    if (!hud) return ReplayViewerDataWrapper(0);
    return hud.GetViewerData();
}

// Focus id for target_: used as-is if it is already an id, otherwise looked up
// by player name among the replay's players. Empty until the players exist.
std::string RLVid::ResolveTarget()
{
    if (target_.rfind("Player_", 0) == 0) return target_;
    auto server = gameWrapper->GetGameEventAsReplay();
    if (!server) return "";
    auto pris = server.GetPRIs();
    for (int i = 0; i < pris.Count(); ++i) {
        auto pri = pris.Get(i);
        if (pri && NameKey(pri.GetPlayerName().ToString()) == NameKey(target_))
            return "Player_" + pri.GetUniqueIdWrapper().GetIdString();
    }
    return "";
}

void RLVid::Enforce()
{
    if (target_.empty()) return;
    if (awaitingLoad_) {
        // Still showing the previous replay; the new one starts after a loading screen.
        if (!gameWrapper->IsInReplay()) awaitingLoad_ = false;
        return;
    }
    if (!gameWrapper->IsInReplay()) {
        if (seenReplay_) {  // replay ended or was exited: stop controlling the camera
            target_.clear();
            if (lastEvent_ == "focused") lastEvent_ = "replay_left";
        }
        return;
    }
    auto viewer = Viewer();
    if (!viewer) return;
    if (!seenReplay_) {
        seenReplay_ = true;
        if (holdAtStart_) {
            viewer.SetPausedForScrub(1);
            cvarManager->log("rlvid: holding replay on its first frame");
        }
    }

    if (viewer.GetbShowReplayHUD()) viewer.SetShowReplayHUD(0);
    if (!viewer.GetbShowPlayerNames()) viewer.SetShowPlayerNames(1);
    if (!viewer.GetbShowMatchInfoHUD()) viewer.SetShowMatchInfoHUD(1);

    if (focusId_.empty()) {
        focusId_ = ResolveTarget();
        if (focusId_.empty()) return;  // players not replicated yet; try next frame
    }
    UpdateKickoff();
    bool changed = false;
    if (inKickoff_) {
        // Clear the focus too: with the target still focused, the game treats the
        // Director shot as "watching the target" (their boost meter shown, their
        // nameplate hidden) even while it shows other cars. Clearing the focus
        // switches the game to Fly, so set the Director again in the same tick
        // (before anything is rendered).
        if (!viewer.GetFocusActorString().empty()) { viewer.SetFocusActorString(""); changed = true; }
        if (viewer.GetCameraMode() != kDirectorMode) { viewer.SetCameraMode(kDirectorMode); changed = true; }
    } else {
        // Focus before mode: Player View is disabled while nothing is focused.
        if (viewer.GetFocusActorString() != focusId_) { viewer.SetFocusActorString(focusId_); changed = true; }
        if (viewer.GetCameraMode() != kCameraMode) { viewer.SetCameraMode(kCameraMode); changed = true; }
    }

    LogCameraChange(viewer);

    // Switches we make on purpose (kickoff <-> player) are not corrections.
    bool phaseSwitched = inKickoff_ != wasKickoff_;
    wasKickoff_ = inKickoff_;
    if (!changed && !locked_) {
        locked_ = true;
        lastEvent_ = "focused";
        cvarManager->log("rlvid: camera locked on " + focusId_ + " (" + FocusedName() + ")"
                         + (inKickoff_ ? ", starting with kickoff director" : ""));
    } else if (changed && locked_ && !phaseSwitched) {
        ++corrections_;
    }
}

// Logs every change of camera mode or focused player, with the replay frame,
// to the BakkesMod console/log so camera behaviour can be checked afterwards.
void RLVid::LogCameraChange(ReplayViewerDataWrapper& viewer)
{
    std::string mode = viewer.GetCameraMode();
    std::string name = FocusedName();
    if (mode == lastLoggedMode_ && name == lastLoggedName_) return;
    lastLoggedMode_ = mode;
    lastLoggedName_ = name;
    auto server = gameWrapper->GetGameEventAsReplay();
    int frame = server ? server.GetCurrentReplayFrame() : -1;
    cvarManager->log("rlvid cam: frame " + std::to_string(frame) + " mode " + mode + " focus '" + name + "'"
                     + (inKickoff_ ? " [kickoff]" : ""));
}

// Kickoff = ball resting on the centre spot. The kickoff view lasts until
// kickoffSeconds_ after the ball first leaves the spot. Frame-based, so it
// follows replay time rather than wall-clock time.
void RLVid::UpdateKickoff()
{
    if (kickoffSeconds_ <= 0) { inKickoff_ = false; return; }
    auto server = gameWrapper->GetGameEventAsReplay();
    if (!server) return;
    int frame = server.GetCurrentReplayFrame();
    auto ball = server.GetBall();
    if (!ball) return;  // e.g. during the goal explosion: keep the current view
    Vector loc = ball.GetLocation();
    bool atCentre = std::abs(loc.X) < 20 && std::abs(loc.Y) < 20;

    if (atCentre) {
        inKickoff_ = true;
        releaseFrame_ = -1;
    } else if (inKickoff_) {
        int fps = server.GetReplayFPS() > 0 ? server.GetReplayFPS() : 30;
        if (releaseFrame_ < 0) releaseFrame_ = frame + (int)(kickoffSeconds_ * fps);
        if (frame >= releaseFrame_ || frame < releaseFrame_ - 10 * fps) {  // second check: scrubbed backwards
            inKickoff_ = false;
            releaseFrame_ = -1;
        }
    }
}

std::string RLVid::FocusedName()
{
    auto pc = gameWrapper->GetPlayerController();
    if (!pc) return "";
    auto hud = pc.GetSpectatorHud();
    if (!hud) return "";
    auto car = hud.GetFocusCar();
    if (!car) return "";
    auto pri = car.GetPRI();
    if (!pri) return "";
    return pri.GetPlayerName().ToString();
}

// Resumes a replay that rlvid_play held on its first frame.
void RLVid::Release()
{
    auto viewer = Viewer();
    if (!viewer) { lastEvent_ = "release_error: not in replay"; return; }
    viewer.SetPausedForScrub(0);
    holdAtStart_ = false;
    cvarManager->log("rlvid: released");
}

void RLVid::Info()
{
    if (!gameWrapper->IsInReplay()) { cvarManager->log("rlvid_info: not in a replay"); return; }
    auto viewer = Viewer();
    if (viewer) {
        cvarManager->log("camera mode: " + viewer.GetCameraMode() + "  focus string: " + viewer.GetFocusActorString());
        for (auto& m : viewer.GetCameraModes())
            cvarManager->log("  mode " + m.name + " (" + m.label + ")" + (m.disabled ? " disabled" : ""));
    }
    cvarManager->log("focused player: " + FocusedName() + "  target: " + target_ + " -> " + focusId_);
    auto server = gameWrapper->GetGameEventAsReplay();
    if (server) {
        auto pris = server.GetPRIs();
        for (int i = 0; i < pris.Count(); ++i) {
            auto pri = pris.Get(i);
            if (!pri) continue;
            cvarManager->log("  player " + pri.GetPlayerName().ToString() + " team " + std::to_string(pri.GetTeamNum())
                             + " id " + pri.GetUniqueIdWrapper().GetIdString());
        }
    }
}

void RLVid::WriteStatus()
{
    bool inReplay = gameWrapper->IsInReplay();
    int frame = -1, numFrames = -1;
    float fps = 0, elapsed = 0;
    std::string focusString, cameraMode;
    if (inReplay) {
        auto server = gameWrapper->GetGameEventAsReplay();
        if (server) {
            frame = server.GetCurrentReplayFrame();
            elapsed = server.GetReplayTimeElapsed();
            fps = (float)server.GetReplayFPS();
            auto replay = server.GetReplay();
            if (replay) numFrames = replay.GetNumFrames();
        }
        auto viewer = Viewer();
        if (viewer) {
            focusString = viewer.GetFocusActorString();
            cameraMode = viewer.GetCameraMode();
        }
    }
    float targetBoost = -1;
    int targetBoostReplicated = -1;
    if (inReplay && !focusId_.empty()) {
        auto server = gameWrapper->GetGameEventAsReplay();
        if (server) {
            auto cars = server.GetCars();
            for (int i = 0; i < cars.Count(); ++i) {
                auto car = cars.Get(i);
                if (!car) continue;
                auto pri = car.GetPRI();
                if (!pri || "Player_" + pri.GetUniqueIdWrapper().GetIdString() != focusId_) continue;
                auto boost = car.GetBoostComponent();
                if (boost) {
                    targetBoost = boost.GetCurrentBoostAmount();
                    targetBoostReplicated = boost.GetReplicatedBoostAmount();
                }
            }
        }
    }
    std::ostringstream js;
    js << "{\"in_replay\": " << (inReplay ? "true" : "false")
       << ", \"frame\": " << frame
       << ", \"num_frames\": " << numFrames
       << ", \"fps\": " << fps
       << ", \"elapsed\": " << elapsed
       << ", \"focused\": \"" << JsonEscape(inReplay ? FocusedName() : "") << "\""
       << ", \"focus_id\": \"" << JsonEscape(focusString) << "\""
       << ", \"camera_mode\": \"" << JsonEscape(cameraMode) << "\""
       << ", \"locked\": " << (locked_ ? "true" : "false")
       << ", \"corrections\": " << corrections_
       << ", \"paused\": " << (inReplay && Viewer() && Viewer().GetbPausedForScrub() ? "true" : "false")
       << ", \"target_boost\": " << targetBoost
       << ", \"target_boost_replicated\": " << targetBoostReplicated
       << ", \"kickoff\": " << (inKickoff_ ? "true" : "false")
       << ", \"event\": \"" << JsonEscape(lastEvent_) << "\""
       << ", \"time\": " << (long long)std::time(nullptr) << "}";

    auto path = StatusPath();
    auto tmp = path;
    tmp += ".tmp";
    {
        std::ofstream f(tmp, std::ios::trunc);
        f << js.str();
    }
    std::error_code ec;
    std::filesystem::rename(tmp, path, ec);
}
