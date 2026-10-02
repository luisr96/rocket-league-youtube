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
#include <bakkesmod/wrappers/GameEvent/ReplaySoccarWrapper.h>

#include <cctype>
#include <cmath>
#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <vector>

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
    void Settle(ReplayViewerDataWrapper& viewer);
    void TrackGoals();
    CarWrapper TargetCar();
    std::string ViewTargetName();
    std::string DirectorCarName();
    std::string ResolveTarget();
    void Info();
    void Release();
    void Tick();
    void WriteStatus();
    void WriteHud();
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
    bool kickoffKeepFocus_ = false;  // rlvid_kickoff_keep_focus 1: keep the target focused during the kickoff Director
    bool hideScoreboard_ = false;    // rlvid_hide_scoreboard 1: the game's match info (scoreboard) off
    bool wasKickoff_ = false;
    int releaseFrame_ = -1;
    int settleTicks_ = 0;        // >0: just switched to Player View; check the car actually shown each tick
    int settleTick_ = 0;         // ticks since that switch (for the log)
    int settleNextFix_ = 0;      // settleTick_ at which the next re-apply is allowed
    int settleFixes_ = 0;        // re-applies needed after switches (status file)
    std::string lastSettleLog_;
    std::string lastDirectorView_ = "-";

    // Goals seen while the replay plays (for thumbnails): replay time, team,
    // scorer and speed (kph).
    struct Goal { int frame; float elapsed; double wall; int team; std::string scorer, scorerId; float speed; };
    std::vector<Goal> goals_;
    int lastScore_[2] = {-1, -1};
    float lastBallSpeed_ = 0;
    std::string lastLoggedMode_, lastLoggedName_;        // times the view had to be put back after locking
    std::string lastEvent_ = "loaded";
    std::chrono::steady_clock::time_point lastWrite_{};
    std::chrono::steady_clock::time_point lastHud_{};
};

BAKKESMOD_PLUGIN(RLVid, "RLVid replay recorder helper", "1.1", PLUGINTYPE_REPLAY)

static const char* kTickEvent = "Function Engine.GameViewportClient.Tick";
static const char* kCameraMode = "PlayerView";
static const char* kDirectorMode = "Camera_Director";
static const int kSettleTicks = 90;     // ~1.5 s at 60 fps: how long to watch the car shown after a switch
static const int kSettleFixEvery = 5;   // ticks between re-applies while the wrong car is shown

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

    cvarManager->registerCvar("rlvid_kickoff_keep_focus", "0",
        "1: keep the target focused during the kickoff Director shot (no re-targeting when it ends)",
        true, true, 0, true, 1).addOnValueChanged([this](std::string, CVarWrapper cvar) {
        kickoffKeepFocus_ = cvar.getBoolValue();
    });

    cvarManager->registerCvar("rlvid_hide_scoreboard", "0",
        "1: hide the game's scoreboard (match info) in replays, e.g. when an overlay draws its own",
        true, true, 0, true, 1).addOnValueChanged([this](std::string, CVarWrapper cvar) {
        hideScoreboard_ = cvar.getBoolValue();
    });

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
    if (now - lastHud_ >= std::chrono::milliseconds(33)) {  // ~30 times a second, so boost keeps up
        lastHud_ = now;
        WriteHud();
    }
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
    settleTicks_ = settleFixes_ = 0;
    goals_.clear();
    lastScore_[0] = lastScore_[1] = -1;
    lastBallSpeed_ = 0;
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
    if ((bool)viewer.GetbShowMatchInfoHUD() == hideScoreboard_) viewer.SetShowMatchInfoHUD(hideScoreboard_ ? 0 : 1);

    if (focusId_.empty()) {
        focusId_ = ResolveTarget();
        if (focusId_.empty()) return;  // players not replicated yet; try next frame
    }
    UpdateKickoff();
    TrackGoals();
    bool changed = false, deliberate = false;
    if (inKickoff_) {
        // Clear the focus too: with the target still focused, the game treats the
        // Director shot as "watching the target" (their boost meter shown, their
        // nameplate hidden) even while it shows other cars. Clearing the focus
        // switches the game to Fly, so set the Director again in the same tick
        // (before anything is rendered).
        // (Unless rlvid_kickoff_keep_focus is set: then the target stays focused.)
        std::string kickoffFocus = kickoffKeepFocus_ ? focusId_ : "";
        if (viewer.GetFocusActorString() != kickoffFocus) { viewer.SetFocusActorString(kickoffFocus); changed = true; }
        if (viewer.GetCameraMode() != kDirectorMode) { viewer.SetCameraMode(kDirectorMode); changed = true; }
    } else if (viewer.GetCameraMode() != kCameraMode) {
        // Leaving the Director (kickoff over, or first lock): target first, and
        // switch to Player View only on the next tick. Switching in the same tick
        // made Player View start from the car the Director was showing and pass
        // through other players, with the boost meter left on one of them.
        if (viewer.GetFocusActorString() != focusId_) {
            viewer.SetFocusActorString(focusId_);
            if (viewer.GetCameraMode() != kDirectorMode) viewer.SetCameraMode(kDirectorMode);  // focus change may switch it to Fly
            auto server = gameWrapper->GetGameEventAsReplay();
            auto director = server ? server.GetReplayDirector() : ReplayDirectorWrapper(0);
            if (director && TargetCar()) director.SetFocusCar(TargetCar());
        } else {
            viewer.SetCameraMode(kCameraMode);
            settleTicks_ = kSettleTicks;
            settleTick_ = settleNextFix_ = 0;
            lastSettleLog_.clear();
        }
        changed = deliberate = true;
    } else if (viewer.GetFocusActorString() != focusId_) {
        viewer.SetFocusActorString(focusId_);
        changed = true;
    }

    LogCameraChange(viewer);
    if (!inKickoff_ && settleTicks_ > 0) Settle(viewer);
    if (inKickoff_) {
        // Diagnostics: does the camera's view target name the car the Director shows?
        std::string view = ViewTargetName();
        if (view != lastDirectorView_) {
            lastDirectorView_ = view;
            auto server = gameWrapper->GetGameEventAsReplay();
            cvarManager->log("rlvid director: frame " + std::to_string(server ? server.GetCurrentReplayFrame() : -1)
                             + " view '" + view + "'");
        }
    } else {
        lastDirectorView_ = "-";
    }

    // Switches we make on purpose (kickoff <-> player) are not corrections.
    bool phaseSwitched = inKickoff_ != wasKickoff_;
    wasKickoff_ = inKickoff_;
    if (!changed && !locked_) {
        locked_ = true;
        lastEvent_ = "focused";
        cvarManager->log("rlvid: camera locked on " + focusId_ + " (" + FocusedName() + ")"
                         + (inKickoff_ ? ", starting with kickoff director" : ""));
    } else if (changed && locked_ && !phaseSwitched && !deliberate) {
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
    // At a kickoff the ball rests on the centre spot (Z ~93). Checking only X/Y
    // also caught the ball passing over the centre in play (switching to the
    // Director mid-game), so it must be low and still too.
    Vector loc = ball.GetLocation();
    Vector vel = ball.GetVelocity();
    bool atCentre = std::abs(loc.X) < 20 && std::abs(loc.Y) < 20 && loc.Z < 120
                    && std::abs(vel.X) + std::abs(vel.Y) + std::abs(vel.Z) < 10;

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

// Goals, detected as a team's score going up. The game's live score data is
// not filled in during replay playback, so the scorer comes from the replay
// file's own goal list (frame + player name), matched by team and nearest
// frame, and the speed is the ball's speed on the tick before the goal.
void RLVid::TrackGoals()
{
    auto server = gameWrapper->GetGameEventAsReplay();
    if (!server) return;
    auto teams = server.GetTeams();
    if (teams.Count() < 2) return;

    for (int t = 0; t < 2; ++t) {
        auto team = teams.Get(t);
        if (!team) continue;
        int idx = team.GetTeamNum2() == 1 ? 1 : 0;
        int score = team.GetScore();
        if (lastScore_[idx] >= 0 && score == lastScore_[idx] + 1) {
            // wall = real time (Unix seconds) when the goal was seen; the video runs in real
            // time, while the replay's own elapsed time does not advance steadily.
            double wall = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
            Goal g{server.GetCurrentReplayFrame(), server.GetReplayTimeElapsed(), wall, idx, "", "", lastBallSpeed_ * 0.036f};
            auto replay = server.GetReplay();
            if (replay) {
                int best = 1 << 30;
                for (auto& sg : ReplaySoccarWrapper(replay.memory_address).GetGoals()) {
                    int d = std::abs(sg.frame - g.frame);
                    if (sg.player_team == idx && d < best && d < 300) { best = d; g.scorer = sg.player_name; }
                }
            }
            auto pris = server.GetPRIs();
            for (int i = 0; i < pris.Count() && !g.scorer.empty(); ++i) {
                auto pri = pris.Get(i);
                if (pri && pri.GetTeamNum() == idx && NameKey(pri.GetPlayerName().ToString()) == NameKey(g.scorer)) {
                    g.scorerId = "Player_" + pri.GetUniqueIdWrapper().GetIdString();
                    g.scorer = pri.GetPlayerName().ToString();  // as the game shows it
                }
            }
            goals_.push_back(g);
            cvarManager->log("rlvid goal: frame " + std::to_string(g.frame) + " team " + std::to_string(idx)
                             + " by '" + g.scorer + "' (" + g.scorerId + ") " + std::to_string((int)g.speed) + " kph");
        }
        lastScore_[idx] = score;
    }
    auto ball = server.GetBall();
    if (ball) lastBallSpeed_ = ball.GetVelocity().magnitude();  // uu/s; kept while the ball is gone after a goal
}

CarWrapper RLVid::TargetCar()
{
    auto server = gameWrapper->GetGameEventAsReplay();
    if (!server || focusId_.empty()) return CarWrapper(0);
    auto cars = server.GetCars();
    for (int i = 0; i < cars.Count(); ++i) {
        auto car = cars.Get(i);
        if (!car) continue;
        auto pri = car.GetPRI();
        if (pri && "Player_" + pri.GetUniqueIdWrapper().GetIdString() == focusId_) return car;
    }
    return CarWrapper(0);
}

// Player the camera is actually rendering (may differ from the focus setting).
std::string RLVid::ViewTargetName()
{
    auto cam = gameWrapper->GetCamera();
    if (!cam) return "";
    auto vt = cam.GetViewTarget();
    if (!vt.PRI) return "";
    PriWrapper pri((std::uintptr_t)vt.PRI);
    return pri ? pri.GetPlayerName().ToString() : "";
}

std::string RLVid::DirectorCarName()
{
    auto server = gameWrapper->GetGameEventAsReplay();
    auto director = server ? server.GetReplayDirector() : ReplayDirectorWrapper(0);
    if (!director) return "";
    CarWrapper car(director.GetFocusCar().memory_address);
    if (!car) return "";
    auto pri = car.GetPRI();
    return pri ? pri.GetPlayerName().ToString() : "";
}

// For a short while after switching to Player View, check every tick which
// car is really shown (camera view target) and whose HUD/boost meter is up
// (HUD focus car), logging each change. If either is not the target, re-apply
// the target: set the focus again, point the Director at the target car, and
// if that is not enough, clear and re-set the focus and mode in one tick (so
// nothing in between is rendered).
void RLVid::Settle(ReplayViewerDataWrapper& viewer)
{
    --settleTicks_;
    ++settleTick_;
    auto server = gameWrapper->GetGameEventAsReplay();
    auto target = TargetCar();
    std::string want = target && target.GetPRI() ? target.GetPRI().GetPlayerName().ToString() : "";
    std::string hud = FocusedName(), view = ViewTargetName(), director = DirectorCarName();

    std::string line = "hud '" + hud + "' view '" + view + "' director '" + director + "'";
    if (line != lastSettleLog_) {
        lastSettleLog_ = line;
        cvarManager->log("rlvid settle: tick " + std::to_string(settleTick_) + " frame "
                         + std::to_string(server ? server.GetCurrentReplayFrame() : -1) + " " + line);
    }
    if (want.empty() || (hud == want && view == want) || settleTick_ < settleNextFix_) return;

    // Harder re-apply from the second attempt on: clear first so the game
    // rebinds the HUD, then target and Player View again, all before rendering.
    bool hard = settleNextFix_ > 0;
    if (hard) viewer.SetFocusActorString("");
    viewer.SetFocusActorString(focusId_);
    viewer.SetCameraMode(kCameraMode);
    auto dir = server ? server.GetReplayDirector() : ReplayDirectorWrapper(0);
    if (dir) dir.SetFocusCar(target);
    ++settleFixes_;
    settleNextFix_ = settleTick_ + kSettleFixEvery;
    cvarManager->log(std::string("rlvid settle: wrong car shown, re-applied target") + (hard ? " (clear + set)" : ""));
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

// Live data for the broadcast overlay (overlay/hud.html), written ~30 times a
// second to bakkesmod/data/rlvid_hud.json: scores, clock, and each player's
// team, boost (0-100, rounded down like the game's boost meter; -1 while their
// car is gone) and match stats.
void RLVid::WriteHud()
{
    std::ostringstream js;
    bool inReplay = gameWrapper->IsInReplay();
    js << "{\"in_replay\": " << (inReplay ? "true" : "false");
    if (inReplay) {
        auto server = gameWrapper->GetGameEventAsReplay();
        if (server) {
        int score[2] = {0, 0};
        auto teams = server.GetTeams();
        for (int t = 0; t < teams.Count(); ++t) {
            auto team = teams.Get(t);
            if (team) score[team.GetTeamNum2() == 1 ? 1 : 0] = team.GetScore();
        }
        js << ", \"score\": {\"blue\": " << score[0] << ", \"orange\": " << score[1] << "}"
           << ", \"clock\": " << server.GetSecondsRemaining()
           << ", \"overtime\": " << (server.GetbOverTime() ? "true" : "false")
           << ", \"players\": [";
        auto pris = server.GetPRIs();
        bool first = true;
        for (int i = 0; i < pris.Count(); ++i) {
            auto pri = pris.Get(i);
            if (!pri) continue;
            int team = pri.GetTeamNum();
            if (team != 0 && team != 1) continue;  // spectators
            float boost = -1;
            auto car = pri.GetCar();
            if (car) {
                auto b = car.GetBoostComponent();
                if (b) {
                    boost = b.GetCurrentBoostAmount();
                    if (boost <= 1.0f) boost *= 100;  // 0-1 in most versions
                }
            }
            js << (first ? "" : ", ") << "{\"name\": \"" << JsonEscape(pri.GetPlayerName().ToString())
               << "\", \"id\": \"Player_" << JsonEscape(pri.GetUniqueIdWrapper().GetIdString())
               << "\", \"team\": " << team << ", \"boost\": " << (boost < 0 ? -1 : (int)std::floor(boost + 1e-3f))
               << ", \"score\": " << pri.GetMatchScore() << ", \"goals\": " << pri.GetMatchGoals()
               << ", \"assists\": " << pri.GetMatchAssists() << ", \"saves\": " << pri.GetMatchSaves()
               << ", \"shots\": " << pri.GetMatchShots() << "}";
            first = false;
        }
        js << "]";
        }
    }
    js << ", \"time\": " << (long long)std::time(nullptr) << "}";

    auto path = gameWrapper->GetDataFolder() / "rlvid_hud.json";
    auto tmp = path;
    tmp += ".tmp";
    {
        std::ofstream f(tmp, std::ios::trunc);
        f << js.str();
    }
    std::error_code ec;
    std::filesystem::rename(tmp, path, ec);
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
    // Players as the game shows them (name tags), for the overlay. Team 0 blue,
    // 1 orange; anyone else (e.g. the local spectator) is left out.
    std::ostringstream players;
    players << "[";
    if (inReplay) {
        auto server = gameWrapper->GetGameEventAsReplay();
        if (server) {
            auto pris = server.GetPRIs();
            bool first = true;
            for (int i = 0; i < pris.Count(); ++i) {
                auto pri = pris.Get(i);
                if (!pri) continue;
                int team = pri.GetTeamNum();
                if (team != 0 && team != 1) continue;
                players << (first ? "" : ", ") << "{\"name\": \"" << JsonEscape(pri.GetPlayerName().ToString())
                        << "\", \"team\": " << team << ", \"id\": \"Player_"
                        << JsonEscape(pri.GetUniqueIdWrapper().GetIdString()) << "\"}";
                first = false;
            }
        }
    }
    players << "]";

    std::ostringstream goals;
    goals << "[";
    for (size_t i = 0; i < goals_.size(); ++i) {
        auto& g = goals_[i];
        goals << (i ? ", " : "") << "{\"frame\": " << g.frame << ", \"elapsed\": " << g.elapsed
              << ", \"wall\": " << std::fixed << std::setprecision(3) << g.wall
              << std::defaultfloat << std::setprecision(6)  // back to normal for the fields after it
              << ", \"team\": " << g.team << ", \"scorer\": \"" << JsonEscape(g.scorer)
              << "\", \"scorer_id\": \"" << JsonEscape(g.scorerId) << "\", \"speed\": " << g.speed << "}";
    }
    goals << "]";

    std::ostringstream js;
    js << "{\"in_replay\": " << (inReplay ? "true" : "false")
       << ", \"goals\": " << goals.str()
       << ", \"players\": " << players.str()
       << ", \"frame\": " << frame
       << ", \"num_frames\": " << numFrames
       << ", \"fps\": " << fps
       << ", \"elapsed\": " << elapsed
       << ", \"focused\": \"" << JsonEscape(inReplay ? FocusedName() : "") << "\""
       << ", \"focus_id\": \"" << JsonEscape(focusString) << "\""
       << ", \"camera_mode\": \"" << JsonEscape(cameraMode) << "\""
       << ", \"locked\": " << (locked_ ? "true" : "false")
       << ", \"corrections\": " << corrections_
       << ", \"settle_fixes\": " << settleFixes_
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
