#!/usr/bin/env python3
import os
import json
import argparse

DEFAULT_CONFIG = {
    "title": "HYPERPIPELINE",
    "intro_text": "THE FUTURE OF VIDEO GENERATION",
    "drop_texts": [
        "COMPILED",
        "FROM CODE",
        "HTML + CSS",
        "GSAP MOTOR",
        "NO DIFFUSION",
        "ZERO LAG",
        "FRAME SYNC",
        "Tiktok READY"
    ],
    "outro_text": "Follow @alex_ai",
    "theme": {
        "color_primary": "#ff3b00",   # Neon Orange/Red
        "color_secondary": "#00f0ff", # Neon Cyan
        "color_bg_start": "#080710",
        "color_bg_end": "#0f0c20"
    }
}

def generate_html(analysis_path, config_path, output_path):
    print(f"Generating Multi-Scene HTML composition using: {analysis_path}...")
    
    if not os.path.exists(analysis_path):
        print(f"Error: Analysis JSON {analysis_path} not found.")
        return False
        
    # Load analysis data
    with open(analysis_path, "r") as f:
        analysis = json.load(f)
        
    # Load config data or create default
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
            print(f"Loaded custom config from {config_path}")
    else:
        config = DEFAULT_CONFIG
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
        print(f"Created default config at {config_path}")
        
    # Build HTML content
    title = config.get("title", "HYPERPIPELINE")
    intro_text = config.get("intro_text", "HTML Video Generation")
    drop_texts = config.get("drop_texts", ["DYNAMIC", "SYNCHRONIZED", "BEAT-REACTIVE"])
    outro_text = config.get("outro_text", "Created by AI")
    
    theme = config.get("theme", DEFAULT_CONFIG["theme"])
    color_primary = theme.get("color_primary", "#ff3b00")
    color_secondary = theme.get("color_secondary", "#00f0ff")
    color_bg_start = theme.get("color_bg_start", "#080710")
    color_bg_end = theme.get("color_bg_end", "#0f0c20")
    
    # Calculate scene start and end times dynamically from the analysis
    duration = analysis["duration"]
    drop_time = analysis["drop_time"]
    
    # Define 5 distinct scenes
    # s1: Intro (0 to buildup_start)
    # s2: Buildup (buildup_start to drop_time)
    # s3: Drop Act 1 (drop_time to drop_mid)
    # s4: Drop Act 2 (drop_mid to outro_start)
    # s5: Outro (outro_start to end)
    
    buildup_duration = min(6.0, drop_time)
    s1_start, s1_end = 0.0, drop_time - buildup_duration
    s2_start, s2_end = s1_end, drop_time
    
    drop_duration = min(30.0, duration - drop_time)
    drop_mid = drop_time + (drop_duration / 2.0)
    
    s3_start, s3_end = drop_time, drop_mid
    s4_start, s4_end = drop_mid, drop_time + drop_duration
    s5_start, s5_end = s4_end, duration
    
    # Segment drop texts for Scenes 3 and 4
    mid_idx = len(drop_texts) // 2
    drop_texts_s3 = drop_texts[:mid_idx] if mid_idx > 0 else drop_texts
    drop_texts_s4 = drop_texts[mid_idx:] if mid_idx > 0 else drop_texts
    
    # Serialized JSONs for script embedding
    analysis_json_str = json.dumps(analysis)
    drop_texts_s3_str = json.dumps(drop_texts_s3)
    drop_texts_s4_str = json.dumps(drop_texts_s4)
    
    html_template = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <!-- Cyberpunk / Sci-Fi Monospace Display Fonts -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&display=swap" rel="stylesheet">
  
  <!-- GSAP Animation Library -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>
  
  <style>
    :root {{
      --primary: {color_primary};
      --secondary: {color_secondary};
    }}
    
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    
    body {{
      background: #000;
      color: #fff;
      font-family: 'Share Tech Mono', monospace;
      overflow: hidden;
      display: flex;
      justify-content: center;
      align-items: center;
      width: 100vw;
      height: 100vh;
    }}
    
    /* 9:16 TikTok Aspect Ratio Stage */
    #stage {{
      position: relative;
      width: 1080px;
      height: 1920px;
      background: #06050b;
      overflow: hidden;
      box-shadow: 0 0 100px rgba(0,0,0,0.9);
      transform-origin: center center;
    }}
    
    /* Technical Grid Overlay */
    .grid-bg {{
      position: absolute;
      top: 0; left: 0; width: 100%; height: 100%;
      background-size: 80px 80px;
      background-image: 
        linear-gradient(to right, rgba(0, 240, 255, 0.03) 1px, transparent 1px),
        linear-gradient(to bottom, rgba(0, 240, 255, 0.03) 1px, transparent 1px);
      z-index: 1;
    }}
    
    .grid-radial {{
      position: absolute;
      top: 0; left: 0; width: 100%; height: 100%;
      background: radial-gradient(circle at 50% 50%, transparent 20%, rgba(6, 5, 11, 0.8) 90%);
      z-index: 2;
    }}
    
    /* Film Scanlines Overlay */
    .scanlines {{
      position: absolute;
      top: 0; left: 0; width: 100%; height: 100%;
      background: linear-gradient(
        rgba(18, 16, 16, 0) 50%, 
        rgba(0, 0, 0, 0.3) 50%
      );
      background-size: 100% 6px;
      z-index: 8;
      pointer-events: none;
      opacity: 0.75;
    }}
    
    /* Vignette Shadow */
    .vignette {{
      position: absolute;
      top: 0; left: 0; width: 100%; height: 100%;
      background: radial-gradient(circle, transparent 40%, rgba(0, 0, 0, 0.9) 100%);
      z-index: 7;
      pointer-events: none;
    }}
    
    /* White Flash Overlay */
    #flash-overlay {{
      position: absolute;
      top: 0; left: 0; width: 100%; height: 100%;
      background: #fff;
      mix-blend-mode: overlay;
      z-index: 10;
      opacity: 0;
      pointer-events: none;
    }}
    
    /* ========================================================
       HUD INTERFACE DECORATIONS
       ======================================================== */
       
    .hud-bracket {{
      position: absolute;
      width: 40px;
      height: 40px;
      border: 3px solid var(--primary);
      z-index: 6;
      opacity: 0.55;
    }}
    .bracket-tl {{ top: 40px; left: 40px; border-right: none; border-bottom: none; }}
    .bracket-tr {{ top: 40px; right: 40px; border-left: none; border-bottom: none; }}
    .bracket-bl {{ bottom: 40px; left: 40px; border-right: none; border-top: none; }}
    .bracket-br {{ bottom: 40px; right: 40px; border-left: none; border-top: none; }}
    
    .hud-data {{
      position: absolute;
      font-family: 'Share Tech Mono', monospace;
      font-size: 20px;
      color: rgba(255, 255, 255, 0.35);
      z-index: 6;
      letter-spacing: 2px;
    }}
    .hud-left {{ top: 100px; left: 60px; text-align: left; line-height: 1.8; }}
    .hud-right {{ top: 100px; right: 60px; text-align: right; line-height: 1.8; }}
    .hud-bottom {{ bottom: 100px; left: 60px; width: calc(100% - 120px); display: flex; justify-content: space-between; }}
    
    .neon-text-primary {{ color: var(--primary); text-shadow: 0 0 10px var(--primary); }}
    .neon-text-secondary {{ color: var(--secondary); text-shadow: 0 0 10px var(--secondary); }}
    
    /* Concentric SVG HUD Circles */
    .hud-circle-container {{
      position: absolute;
      top: 50%; left: 50%;
      transform: translate(-50%, -50%);
      width: 800px;
      height: 800px;
      z-index: 3;
      pointer-events: none;
      opacity: 0.15;
    }}
    
    .hud-circle {{
      transform-origin: center center;
    }}
    
    /* Oscilloscope Container */
    .oscilloscope-container {{
      position: absolute;
      width: 100%;
      height: 400px;
      bottom: 220px;
      left: 0;
      z-index: 4;
      display: flex;
      justify-content: center;
      align-items: center;
    }}
    #oscilloscope {{
      width: 1000px;
      height: 300px;
    }}
    
    /* ========================================================
       MULTI-SCENE SYSTEM
       ======================================================== */
    
    .scene {{
      position: absolute;
      top: 0; left: 0;
      width: 100%; height: 100%;
      z-index: 5;
    }}
    
    .scene-content {{
      width: 100%;
      height: 100%;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      padding: 60px;
    }}
    
    /* SCENE 1: INTRO */
    #s1 {{
      visibility: visible;
      opacity: 1;
    }}
    
    .intro-header {{
      font-family: 'Share Tech Mono', monospace;
      font-size: 26px;
      font-weight: 700;
      letter-spacing: 6px;
      color: var(--primary);
      text-shadow: 0 0 10px var(--primary);
      margin-bottom: 40px;
    }}
    
    .intro-title {{
      font-family: 'Orbitron', sans-serif;
      font-size: 76px;
      font-weight: 900;
      text-align: center;
      line-height: 1.2;
      text-transform: uppercase;
      color: #fff;
      text-shadow: 0 0 35px var(--secondary);
    }}
    
    /* SCENE 2: BUILDUP */
    #s2 {{
      visibility: hidden;
    }}
    
    .buildup-box {{
      background: rgba(6, 5, 11, 0.8);
      border: 2px solid var(--secondary);
      border-radius: 20px;
      width: 100%;
      max-width: 900px;
      padding: 80px 40px;
      display: flex;
      flex-direction: column;
      align-items: center;
      box-shadow: 0 0 40px rgba(0, 240, 255, 0.15);
    }}
    
    .buildup-title {{
      font-family: 'Orbitron', sans-serif;
      font-size: 40px;
      font-weight: 900;
      letter-spacing: 4px;
      color: #fff;
      margin-bottom: 60px;
      text-shadow: 0 0 15px var(--secondary);
    }}
    
    .buildup-progress-container {{
      width: 100%;
      height: 20px;
      background: rgba(255, 255, 255, 0.04);
      border-radius: 10px;
      overflow: hidden;
      border: 2px solid var(--primary);
      margin-bottom: 30px;
    }}
    
    .buildup-progress-bar {{
      width: 0%;
      height: 100%;
      background: linear-gradient(90deg, var(--primary), var(--secondary));
      box-shadow: 0 0 15px var(--secondary);
    }}
    
    .buildup-percentage {{
      font-family: 'Share Tech Mono', monospace;
      font-size: 72px;
      font-weight: bold;
      color: var(--secondary);
      text-shadow: 0 0 25px var(--secondary);
      margin-bottom: 30px;
    }}
    
    .buildup-info {{
      font-size: 26px;
      color: rgba(255, 255, 255, 0.65);
    }}
    
    /* SCENE 3 & 4: THE CLIMAX DROPS */
    #s3, #s4 {{
      visibility: hidden;
    }}
    
    .drop-box {{
      width: 100%;
      max-width: 950px;
      height: 800px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      text-align: center;
      position: relative;
    }}
    
    .drop-word {{
      font-family: 'Orbitron', sans-serif;
      font-size: 130px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 4px;
      line-height: 1.0;
      color: #fff;
      text-shadow: 0 0 45px rgba(255, 255, 255, 0.5);
    }}
    
    .glow-active {{
      color: var(--primary) !important;
      text-shadow: 0 0 60px var(--primary), 0 0 20px var(--primary) !important;
    }}
    
    /* SCENE 5: OUTRO */
    #s5 {{
      visibility: hidden;
    }}
    
    .outro-card {{
      background: rgba(6, 5, 11, 0.85);
      border: 2px solid var(--primary);
      border-radius: 36px;
      width: 100%;
      max-width: 900px;
      padding: 100px 60px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
      height: 1300px;
      box-shadow: 0 0 50px rgba(255, 59, 0, 0.25);
    }}
    
    .outro-status {{
      font-size: 22px;
      color: var(--secondary);
      text-shadow: 0 0 10px var(--secondary);
      letter-spacing: 4px;
    }}
    
    .outro-logo {{
      font-family: 'Orbitron', sans-serif;
      font-size: 46px;
      font-weight: 900;
      letter-spacing: 8px;
      color: #fff;
      text-transform: uppercase;
    }}
    
    .outro-logo span {{
      color: var(--primary);
      text-shadow: 0 0 15px var(--primary);
    }}
    
    .outro-cta-text {{
      font-family: 'Orbitron', sans-serif;
      font-size: 70px;
      font-weight: 900;
      text-align: center;
      line-height: 1.2;
      text-transform: uppercase;
      color: #fff;
      text-shadow: 0 0 30px var(--secondary);
    }}
    
    .outro-btn {{
      background: transparent;
      border: 3px solid var(--primary);
      color: var(--primary);
      font-family: 'Orbitron', sans-serif;
      font-size: 36px;
      font-weight: 900;
      letter-spacing: 4px;
      padding: 24px 60px;
      border-radius: 10px;
      box-shadow: 0 0 30px rgba(255, 59, 0, 0.15);
      cursor: pointer;
    }}
    
    .outro-footer {{
      font-size: 20px;
      color: rgba(255, 255, 255, 0.25);
      letter-spacing: 2px;
    }}
  </style>
</head>
<body>

  <!-- Root composition container with required structural attributes -->
  <div id="stage" data-composition-id="tiktok_video" data-start="0" data-width="1080" data-height="1920">
    <div class="grid-bg"></div>
    <div class="grid-radial"></div>
    <div class="vignette"></div>
    <div class="scanlines"></div>
    <div id="flash-overlay"></div>
    
    <!-- Concentric SVG HUD Circles -->
    <svg class="hud-circle-container" viewBox="0 0 800 800">
      <circle class="hud-circle" id="circle-outer" cx="400" cy="400" r="300" stroke="var(--primary)" stroke-width="2" stroke-dasharray="15 30" fill="none" opacity="0.5"></circle>
      <circle class="hud-circle" id="circle-mid" cx="400" cy="400" r="240" stroke="var(--secondary)" stroke-width="1.5" stroke-dasharray="40 10 5 15" fill="none" opacity="0.4"></circle>
      <circle class="hud-circle" id="circle-inner" cx="400" cy="400" r="180" stroke="var(--primary)" stroke-width="1.2" stroke-dasharray="2 10" fill="none" opacity="0.6"></circle>
    </svg>
    
    <!-- Interactive Oscilloscope Canvas -->
    <div class="oscilloscope-container">
      <canvas id="oscilloscope" width="1000" height="300"></canvas>
    </div>
    
    <!-- Corner Brackets -->
    <div class="hud-bracket bracket-tl"></div>
    <div class="hud-bracket bracket-tr"></div>
    <div class="hud-bracket bracket-bl"></div>
    <div class="hud-bracket bracket-br"></div>
    
    <!-- Technical HUD Panels -->
    <div class="hud-data hud-left">
      <div>SYS_METRIC: <span class="neon-text-primary">ACTIVE</span></div>
      <div>CORE_TEMP: <span id="temp-val">42.8</span> &deg;C</div>
      <div>CPU_LOAD: <span id="cpu-val" class="neon-text-secondary">08%</span></div>
    </div>
    
    <div class="hud-data hud-right">
      <div>HYPERPIPELINE v1.2</div>
      <div>SCENARIO: SECURE</div>
      <div>TIME_REF: <span id="time-val" class="neon-text-primary">0.00s</span></div>
    </div>
    
    <div class="hud-data hud-bottom">
      <div>OUTPUT_MODE: Vertical (9:16)</div>
      <div>STATUS: <span id="status-val" class="neon-text-secondary">SYS: NORMAL</span></div>
    </div>
    
    <!-- ==========================================
         SCENE 1: INTRO (0.00s - {s1_end:.2f}s)
         ========================================== -->
    <div id="s1" class="scene">
      <div class="scene-content">
        <div class="intro-header" id="s1-header">// INJECTING SCENARIO</div>
        <h1 class="intro-title" id="s1-title">{intro_text}</h1>
      </div>
    </div>
    
    <!-- ==========================================
         SCENE 2: BUILDUP ({s2_start:.2f}s - {s2_end:.2f}s)
         ========================================== -->
    <div id="s2" class="scene">
      <div class="scene-content">
        <div class="buildup-box" id="s2-box">
          <h2 class="buildup-title">// CHARGING CORE ENGINE</h2>
          <div class="buildup-progress-container">
            <div class="buildup-progress-bar" id="s2-progress"></div>
          </div>
          <div class="buildup-percentage" id="s2-percent">00%</div>
          <div class="buildup-info">TRACK TEMPO: {analysis['bpm']:.0f} BPM</div>
        </div>
      </div>
    </div>
    
    <!-- ==========================================
         SCENE 3: DROP ACT 1 ({s3_start:.2f}s - {s3_end:.2f}s)
         ========================================== -->
    <div id="s3" class="scene">
      <div class="scene-content">
        <div class="drop-box" id="s3-box">
          <div class="drop-word" id="s3-text">GET READY</div>
        </div>
      </div>
    </div>
    
    <!-- ==========================================
         SCENE 4: DROP ACT 2 ({s4_start:.2f}s - {s4_end:.2f}s)
         ========================================== -->
    <div id="s4" class="scene">
      <div class="scene-content">
        <div class="drop-box" id="s4-box">
          <div class="drop-word" id="s4-text">DYNAMIC</div>
        </div>
      </div>
    </div>
    
    <!-- ==========================================
         SCENE 5: OUTRO ({s5_start:.2f}s - {s5_end:.2f}s)
         ========================================== -->
    <div id="s5" class="scene">
      <div class="scene-content">
        <div class="outro-card" id="s5-card">
          <div class="outro-status">// CORE COMPILED //</div>
          <div class="outro-logo"><span>//</span>{title}</div>
          <div class="outro-cta-text" id="s5-text">{outro_text}</div>
          <div class="outro-btn-container">
            <button class="outro-btn" id="s5-btn">SUBSCRIBE_</button>
          </div>
          <div class="outro-footer">COMPILER SUCCESSFUL &bull; 2026</div>
        </div>
      </div>
    </div>
    
    <!-- REQUIRED: Audio Track element with ID and data attributes -->
    <audio id="bg-audio" data-start="0" data-duration="{duration}" src="assets/audio.mp3"></audio>
  </div>

  <script>
    // Embedded Analysis JSONs
    const musicData = {analysis_json_str};
    const wordsS3 = {drop_texts_s3_str};
    const wordsS4 = {drop_texts_s4_str};
    
    // Seeded random generator for absolute rendering determinism
    let seed = 12345;
    function seededRandom() {{
      const x = Math.sin(seed++) * 10000;
      return x - Math.floor(x);
    }}
    
    // Scramble characters used in cyber decrypt effect
    const cyberChars = "01XZ@#$\\u2588\\u2591\\u2592\\u2593%+*!?";
    function getScrambledString(word) {{
      let res = "";
      for (let i = 0; i < word.length; i++) {{
        res += cyberChars[Math.floor(seededRandom() * cyberChars.length)];
      }}
      return res;
    }}
    
    // Pause state required for deterministic frame rendering
    const tl = gsap.timeline({{ paused: true }});
    
    // --- 0. INITIAL HIDDEN STATES ---
    gsap.set("#s1-header", {{ y: -30, opacity: 0 }});
    gsap.set("#s1-title", {{ y: 40, opacity: 0 }});
    gsap.set("#s2-box", {{ scale: 0.8, opacity: 0 }});
    gsap.set("#s5-card", {{ y: 100, opacity: 0 }});
    
    // ==========================================
    // 1. SCENE BOUNDARY SETS (autoAlpha)
    // ==========================================
    
    // Scene 1 End
    tl.set("#s1", {{ autoAlpha: 0 }}, {s1_end:.4f});
    
    // Scene 2 Start & End
    tl.set("#s2", {{ autoAlpha: 1 }}, {s2_start:.4f});
    tl.set("#s2", {{ autoAlpha: 0 }}, {s2_end:.4f});
    
    // Scene 3 Start & End
    tl.set("#s3", {{ autoAlpha: 1 }}, {s3_start:.4f});
    tl.set("#s3", {{ autoAlpha: 0 }}, {s3_end:.4f});
    
    // Scene 4 Start & End
    tl.set("#s4", {{ autoAlpha: 1 }}, {s4_start:.4f});
    tl.set("#s4", {{ autoAlpha: 0 }}, {s4_end:.4f});
    
    // Scene 5 Start
    tl.set("#s5", {{ autoAlpha: 1 }}, {s5_start:.4f});
    
    // ==========================================
    // 2. SCENE ANIMATIONS
    // ==========================================
    
    // --- SCENE 1 (Intro) ---
    tl.to("#s1-header", {{ y: 0, opacity: 1, duration: 0.8, ease: "power3.out" }}, 0.2);
    tl.to("#s1-title", {{ y: 0, opacity: 1, duration: 1.0, ease: "back.out(1.5)" }}, 0.4);
    tl.to("#s1 .scene-content", {{ scale: 1.03, duration: {s1_end - s1_start:.2f}, ease: "none" }}, 0.0);
    
    // --- SCENE 2 (Buildup) ---
    tl.to("#s2-box", {{ scale: 1.0, opacity: 1, duration: 0.8, ease: "power4.out" }}, {s2_start:.2f} + 0.1);
    tl.to("#s2-progress", {{ width: "100%", duration: {s2_end - s2_start:.2f} - 0.2, ease: "sine.inOut" }}, {s2_start:.2f} + 0.1);
    
    // Animate percentage count-up
    const percentVal = {{ val: 0 }};
    tl.to(percentVal, {{ 
      val: 100, 
      duration: {s2_end - s2_start:.2f} - 0.2, 
      ease: "sine.inOut",
      onUpdate: () => {{
        const el = document.getElementById("s2-percent");
        if (el) el.innerText = Math.floor(percentVal.val).toString().padStart(2, '0') + "%";
      }}
    }}, {s2_start:.2f} + 0.1);
    
    // --- SCENE 5 (Outro) ---
    tl.to("#s5-card", {{ y: 0, opacity: 1, duration: 1.2, ease: "power4.out" }}, {s5_start:.2f} + 0.2);
    const outroDuration = {s5_end - s5_start:.2f};
    const followRepeat = Math.max(0, Math.floor(outroDuration / 1.2) - 1);
    tl.to("#s5-btn", {{ scale: 1.05, duration: 0.6, repeat: followRepeat, yoyo: true, ease: "sine.inOut" }}, {s5_start:.2f} + 0.8);
    tl.to("#stage", {{ opacity: 0, duration: 1.5, ease: "power2.in" }}, {s5_end:.2f} - 1.5);
    
    // ==========================================
    // 3. OSCILLOSCOPE CONTROL (DETREMINISTIC CANVAS)
    // ==========================================
    
    const canvas = document.getElementById("oscilloscope");
    const ctx = canvas.getContext("2d");
    const wave = {{ amplitude: 35, phase: 0, noise: 0 }};
    
    // Linearly animate wave phase over entire video duration
    tl.to(wave, {{ phase: Math.PI * 40, duration: musicData.duration, ease: "none" }}, 0);
    
    // Clear and draw wave on every GSAP update step (determinsitic, frame-locked)
    tl.to({{}}, {{
      duration: musicData.duration,
      onUpdate: () => {{
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--primary').trim();
        ctx.shadowColor = ctx.strokeStyle;
        ctx.shadowBlur = 20;
        ctx.lineWidth = 4;
        
        ctx.beginPath();
        for (let x = 0; x < canvas.width; x++) {{
          const angle = (x / canvas.width) * Math.PI * 6 + wave.phase;
          // Deterministic noise based on static sine frequencies
          const noiseVal = Math.sin(x * 0.4 + wave.phase * 4) * wave.noise * 50;
          const y = canvas.height / 2 + Math.sin(angle) * wave.amplitude + noiseVal;
          
          if (x === 0) {{
            ctx.moveTo(x, y);
          }} else {{
            ctx.lineTo(x, y);
          }}
        }}
        ctx.stroke();
        
        // Update general status displays in HUD
        const timeEl = document.getElementById("time-val");
        if (timeEl) {{
          timeEl.innerText = (tl.time()).toFixed(2) + "s";
        }}
      }}
    }}, 0);
    
    // Constant rotation of SVG HUD circles
    const circleRepeat = Math.max(0, Math.floor(musicData.duration / 10));
    tl.to("#circle-outer", {{ rotation: 360, duration: 25, ease: "none", repeat: circleRepeat }}, 0);
    tl.to("#circle-mid", {{ rotation: -360, duration: 18, ease: "none", repeat: circleRepeat }}, 0);
    tl.to("#circle-inner", {{ rotation: 360, duration: 12, ease: "none", repeat: circleRepeat }}, 0);
    
    // ==========================================
    // 4. BEAT-SYNCED MOTIONS & TYPOGRAPHY SCRAMBLE
    // ==========================================
    
    musicData.beats.forEach((beat, index) => {{
      const t = beat.time;
      const energy = beat.energy;
      
      // Update global HUD data on beat (CPU, load metrics)
      tl.call(() => {{
        const cpuEl = document.getElementById("cpu-val");
        if (cpuEl) cpuEl.innerText = Math.floor(5 + energy * 85).toString().padStart(2, '0') + "%";
        
        const tempEl = document.getElementById("temp-val");
        if (tempEl) tempEl.innerText = (40.2 + energy * 18.5).toFixed(1);
        
        const statusEl = document.getElementById("status-val");
        if (statusEl) {{
          if (energy > 0.8) {{
            statusEl.innerText = "WARNING: OVERLOAD";
            statusEl.className = "neon-text-primary";
          }} else {{
            statusEl.innerText = "SYS: NORMAL";
            statusEl.className = "neon-text-secondary";
          }}
        }}
      }}, null, t);
      
      // A: Buildup pulse (Scene 2)
      if (t >= {s2_start:.4f} && t < {s2_end:.4f}) {{
        tl.to("#s2-box", {{ scale: 1.02 + (0.02 * energy), duration: 0.05, ease: "power1.out", overwrite: "auto" }}, t);
        tl.to("#s2-box", {{ scale: 1.0, duration: 0.15, ease: "power1.inOut", overwrite: "auto" }}, t + 0.05);
        
        // Pulse intermediate HUD circles
        tl.to("#circle-mid", {{ scale: 1.02 + (0.03 * energy), duration: 0.05, ease: "power1.out", overwrite: "auto" }}, t);
        tl.to("#circle-mid", {{ scale: 1.0, duration: 0.15, ease: "power2.inOut", overwrite: "auto" }}, t + 0.05);
      }}
      
      // B: Climax Drop Act 1 (Scene 3)
      if (t >= {s3_start:.4f} && t < {s3_end:.4f}) {{
        // Flash overlay
        tl.fromTo("#flash-overlay", 
          {{ opacity: 0 }}, 
          {{ opacity: 0.22 * energy, duration: 0.03, yoyo: true, repeat: 1 }}, 
          t
        );
        
        // Digital Aberration / Glitch Shake
        const shakeX = (seededRandom() - 0.5) * 40 * energy;
        const shakeY = (seededRandom() - 0.5) * 40 * energy;
        const skewX = (seededRandom() - 0.5) * 10 * energy;
        const hueRot = (seededRandom() - 0.5) * 60 * energy;
        
        tl.to("#stage", {{ 
          x: shakeX, 
          y: shakeY, 
          skewX: skewX,
          filter: "hue-rotate(" + hueRot + "deg) contrast(" + (1 + 0.4 * energy) + ")", 
          duration: 0.03, 
          ease: "none", 
          overwrite: "auto" 
        }}, t);
        
        tl.to("#stage", {{ 
          x: 0, 
          y: 0, 
          skewX: 0,
          filter: "none", 
          duration: 0.08, 
          ease: "power2.out", 
          overwrite: "auto" 
        }}, t + 0.03);
        
        // Spike oscilloscope amplitude and noise on beat
        tl.to(wave, {{ amplitude: 90 * energy, noise: 0.4 * energy, duration: 0.04, ease: "power1.out", overwrite: "auto" }}, t);
        tl.to(wave, {{ amplitude: 35, noise: 0, duration: 0.22, ease: "power2.inOut", overwrite: "auto" }}, t + 0.04);
        
        // Pulse mid circle scale
        tl.to("#circle-mid", {{ scale: 1.05 + (0.05 * energy), duration: 0.04, ease: "power1.out", overwrite: "auto" }}, t);
        tl.to("#circle-mid", {{ scale: 1.0, duration: 0.2, ease: "power2.inOut", overwrite: "auto" }}, t + 0.04);
        
        // Glitch Decrypt Text swap (scrambling words)
        const wordIndex = index % wordsS3.length;
        const targetWord = wordsS3[wordIndex];
        const scr1 = getScrambledString(targetWord);
        const scr2 = getScrambledString(targetWord);
        
        tl.call(() => {{
          const txtEl = document.getElementById("s3-text");
          if (txtEl) txtEl.innerText = scr1;
        }}, null, t);
        
        tl.call(() => {{
          const txtEl = document.getElementById("s3-text");
          if (txtEl) txtEl.innerText = scr2;
        }}, null, t + 0.04);
        
        tl.call(() => {{
          const txtEl = document.getElementById("s3-text");
          if (txtEl) {{
            txtEl.innerText = targetWord;
            txtEl.className = (wordIndex % 2 === 0) ? "drop-word glow-active" : "drop-word";
          }}
        }}, null, t + 0.08);
      }}
      
      // C: Climax Drop Act 2 (Scene 4)
      if (t >= {s4_start:.4f} && t < {s4_end:.4f}) {{
        // Flash overlay (more energetic)
        tl.fromTo("#flash-overlay", 
          {{ opacity: 0 }}, 
          {{ opacity: 0.28 * energy, duration: 0.03, yoyo: true, repeat: 1 }}, 
          t
        );
        
        // Heavier digital glitch shake
        const shakeX = (seededRandom() - 0.5) * 55 * energy;
        const shakeY = (seededRandom() - 0.5) * 55 * energy;
        const skewX = (seededRandom() - 0.5) * 14 * energy;
        const hueRot = (seededRandom() - 0.5) * 90 * energy;
        
        tl.to("#stage", {{ 
          x: shakeX, 
          y: shakeY, 
          skewX: skewX,
          filter: "hue-rotate(" + hueRot + "deg) contrast(" + (1.1 + 0.5 * energy) + ")", 
          duration: 0.03, 
          ease: "none", 
          overwrite: "auto" 
        }}, t);
        
        tl.to("#stage", {{ 
          x: 0, 
          y: 0, 
          skewX: 0,
          filter: "none", 
          duration: 0.08, 
          ease: "power2.out", 
          overwrite: "auto" 
        }}, t + 0.03);
        
        // Spike oscilloscope amplitude and noise (more noise)
        tl.to(wave, {{ amplitude: 110 * energy, noise: 0.6 * energy, duration: 0.04, ease: "power1.out", overwrite: "auto" }}, t);
        tl.to(wave, {{ amplitude: 35, noise: 0, duration: 0.25, ease: "power2.inOut", overwrite: "auto" }}, t + 0.04);
        
        // Pulse circles opposite directions
        tl.to("#circle-mid", {{ scale: 1.08 + (0.06 * energy), duration: 0.04, ease: "power1.out", overwrite: "auto" }}, t);
        tl.to("#circle-mid", {{ scale: 1.0, duration: 0.22, ease: "power2.inOut", overwrite: "auto" }}, t + 0.04);
        tl.to("#circle-inner", {{ scale: 0.92 - (0.06 * energy), duration: 0.04, ease: "power1.out", overwrite: "auto" }}, t);
        tl.to("#circle-inner", {{ scale: 1.0, duration: 0.18, ease: "power2.inOut", overwrite: "auto" }}, t + 0.04);
        
        // Glitch Decrypt Text swap (S4 list)
        const wordIndex = index % wordsS4.length;
        const targetWord = wordsS4[wordIndex];
        const scr1 = getScrambledString(targetWord);
        const scr2 = getScrambledString(targetWord);
        
        tl.call(() => {{
          const txtEl = document.getElementById("s4-text");
          if (txtEl) txtEl.innerText = scr1;
        }}, null, t);
        
        tl.call(() => {{
          const txtEl = document.getElementById("s4-text");
          if (txtEl) txtEl.innerText = scr2;
        }}, null, t + 0.04);
        
        tl.call(() => {{
          const txtEl = document.getElementById("s4-text");
          if (txtEl) {{
            txtEl.innerText = targetWord;
            txtEl.className = (wordIndex % 2 === 0) ? "drop-word glow-active" : "drop-word";
          }}
        }}, null, t + 0.08);
      }}
    }});
    
    // Register timeline to HyperFrames
    window.__timelines = window.__timelines || {{}};
    window.__timelines["tiktok_video"] = tl;
  </script>
</body>
</html>
"""
    
    # Save the file
    parent_dir = os.path.dirname(output_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)
        
    with open(output_path, "w") as f:
        f.write(html_template)
        
    print(f"HTML composition successfully generated and saved to: {output_path}")
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Generate HTML/CSS/GSAP composition synced to beat analysis")
    parser.add_argument('--analysis', default="assets/analysis.json", help="Path to input analysis JSON")
    parser.add_argument('--config', default="config.json", help="Path to config JSON")
    parser.add_argument('--output', default="index.html", help="Path to output HTML file")
    args = parser.parse_args()
    
    generate_html(args.analysis, args.config, args.output)
