# Privacy-safe demo media

All names, messages, hostnames and session identifiers in the repository's media are synthetic. The app renders its own native views from `desktop/DemoSupport.swift`. The generator does not capture the screen, use a microphone, connect over SSH, or read Codex sessions.

The floating ring's sample data is also synthetic; it demonstrates the presentation of output activity and is not a benchmark. The video is silent and includes captions.

To reproduce on macOS, after building the application:

```bash
'Agent Orbit.app/Contents/MacOS/AgentOrbit' --export-demo /tmp/agent-orbit-demo
# Authoring dependency only: Pillow
python3 scripts/render_media.py --input /tmp/agent-orbit-demo
swiftc -O -framework AppKit -framework AVFoundation scripts/encode_video.swift -o /tmp/agent-orbit-video
/tmp/agent-orbit-video .media-build/frames docs/media/agent-orbit-demo.mp4
```

For the CLT module-map workaround on affected development environments, use the same overlay arguments as `build_desktop.sh`.

The resulting application remains a standalone application. These media tools are optional developer tools.
