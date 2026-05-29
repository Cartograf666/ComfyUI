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
  <!-- Modern Display Fonts -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;700;900&display=swap" rel="stylesheet">
  
  <!-- GSAP Animation Library -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.2/gsap.min.js"></script>
  
  <style>
    :root {{
      --primary: {color_primary};
      --secondary: {color_secondary};
      --bg-start: {color_bg_start};
      --bg-end: {color_bg_end};
    }}
    
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}
    
    body {{
      background: #000;
      color: #fff;
      font-family: 'Outfit', sans-serif;
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
      background: linear-gradient(135deg, var(--bg-start), var(--bg-end));
      overflow: hidden;
      box-shadow: 0 0 100px rgba(0,0,0,0.8);
    }}
    
    /* Shifting Aurora Gradient Background */
    .aurora-bg {{
      position: absolute;
      width: 200%;
      height: 200%;
      top: -50%;
      left: -50%;
      background: radial-gradient(circle at 20% 20%, rgba(255, 59, 0, 0.15) 0%, transparent 40%),
                  radial-gradient(circle at 80% 80%, rgba(0, 240, 255, 0.15) 0%, transparent 40%),
                  radial-gradient(circle at 50% 50%, rgba(127, 0, 255, 0.08) 0%, transparent 60%);
      filter: blur(80px);
      z-index: 1;
      animation: drift 25s infinite alternate ease-in-out;
    }}
    
    @keyframes drift {{
      0% {{ transform: rotate(0deg) scale(1); }}
      50% {{ transform: rotate(180deg) scale(1.15); }}
      100% {{ transform: rotate(360deg) scale(1); }}
    }}
    
    /* Film Grain Overlay */
    .grain-overlay {{
      position: absolute;
      top: 0; left: 0; width: 100%; height: 100%;
      background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='matrix' values='0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.06 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
      z-index: 10;
      opacity: 0.6;
      pointer-events: none;
    }}
    
    /* Vignette Shadow */
    .vignette {{
      position: absolute;
      top: 0; left: 0; width: 100%; height: 100%;
      background: radial-gradient(circle, transparent 40%, rgba(0, 0, 0, 0.85) 100%);
      z-index: 9;
      pointer-events: none;
    }}
    
    /* White Flash Overlay */
    #flash-overlay {{
      position: absolute;
      top: 0; left: 0; width: 100%; height: 100%;
      background: #fff;
      mix-blend-mode: overlay;
      z-index: 11;
      opacity: 0;
      pointer-events: none;
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
      /* Scene 1 starts visible */
      visibility: visible;
      opacity: 1;
    }}
    
    .intro-header {{
      font-size: 36px;
      font-weight: 700;
      letter-spacing: 8px;
      color: rgba(255, 255, 255, 0.4);
      text-transform: uppercase;
      margin-bottom: 50px;
    }}
    
    .intro-header span {{
      color: var(--primary);
    }}
    
    .intro-title {{
      font-size: 84px;
      font-weight: 900;
      text-align: center;
      line-height: 1.1;
      text-transform: uppercase;
      background: linear-gradient(135deg, #fff 30%, rgba(255, 255, 255, 0.3) 100%);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      text-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
    }}
    
    /* SCENE 2: BUILDUP */
    #s2 {{
      visibility: hidden;
    }}
    
    .buildup-box {{
      background: rgba(255, 255, 255, 0.03);
      backdrop-filter: blur(25px);
      -webkit-backdrop-filter: blur(25px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 40px;
      width: 100%;
      max-width: 900px;
      padding: 80px 40px;
      display: flex;
      flex-direction: column;
      align-items: center;
      box-shadow: 0 40px 80px rgba(0,0,0,0.5);
    }}
    
    .buildup-title {{
      font-size: 54px;
      font-weight: 900;
      letter-spacing: 4px;
      text-transform: uppercase;
      text-align: center;
      margin-bottom: 60px;
      color: #fff;
    }}
    
    .buildup-progress-container {{
      width: 100%;
      height: 16px;
      background: rgba(255, 255, 255, 0.05);
      border-radius: 10px;
      overflow: hidden;
      border: 1px solid rgba(255, 255, 255, 0.1);
      margin-bottom: 40px;
    }}
    
    .buildup-progress-bar {{
      width: 0%;
      height: 100%;
      background: linear-gradient(90deg, var(--primary), var(--secondary));
      box-shadow: 0 0 20px var(--primary);
    }}
    
    .buildup-info {{
      font-size: 28px;
      font-weight: 400;
      color: rgba(255, 255, 255, 0.5);
      letter-spacing: 2px;
    }}
    
    /* SCENE 3 & 4: THE CLIMAX DROPS */
    #s3, #s4 {{
      visibility: hidden;
    }}
    
    .drop-box {{
      width: 100%;
      max-width: 950px;
      height: 1000px;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      text-align: center;
      position: relative;
    }}
    
    .drop-word {{
      font-size: 110px;
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 2px;
      line-height: 1.0;
      color: #fff;
      text-shadow: 0 0 50px rgba(255, 255, 255, 0.3);
      transform-origin: center center;
    }}
    
    /* Special primary glow style */
    .glow-active {{
      color: var(--primary);
      text-shadow: 0 0 60px var(--primary), 0 0 20px var(--primary);
    }}
    
    /* Equalizer dots */
    .visualizer {{
      display: flex;
      gap: 16px;
      height: 120px;
      align-items: flex-end;
      margin-top: 80px;
    }}
    
    .bar {{
      width: 14px;
      height: 14px;
      background: linear-gradient(to top, var(--primary), var(--secondary));
      border-radius: 7px;
      transform-origin: bottom center;
      box-shadow: 0 0 15px rgba(0, 240, 255, 0.3);
    }}
    
    /* SCENE 5: OUTRO */
    #s5 {{
      visibility: hidden;
    }}
    
    .outro-card {{
      background: rgba(255, 255, 255, 0.02);
      backdrop-filter: blur(30px);
      -webkit-backdrop-filter: blur(30px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 48px;
      width: 100%;
      max-width: 900px;
      padding: 100px 60px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
      height: 1300px;
      box-shadow: 0 50px 100px rgba(0,0,0,0.7);
    }}
    
    .outro-logo {{
      font-size: 40px;
      font-weight: 900;
      letter-spacing: 12px;
      color: #fff;
      text-transform: uppercase;
    }}
    
    .outro-logo span {{
      color: var(--primary);
    }}
    
    .outro-cta-text {{
      font-size: 72px;
      font-weight: 900;
      text-align: center;
      line-height: 1.1;
      text-transform: uppercase;
      background: linear-gradient(135deg, #fff 40%, rgba(255,255,255,0.4) 100%);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 60px;
    }}
    
    .outro-btn-container {{
      position: relative;
    }}
    
    .outro-btn {{
      background: linear-gradient(135deg, var(--primary), var(--secondary));
      border: none;
      color: #fff;
      font-family: 'Outfit', sans-serif;
      font-size: 36px;
      font-weight: 900;
      letter-spacing: 3px;
      text-transform: uppercase;
      padding: 28px 72px;
      border-radius: 60px;
      box-shadow: 0 25px 50px rgba(255, 59, 0, 0.45);
      cursor: pointer;
    }}
    
    .outro-footer {{
      font-size: 24px;
      font-weight: 400;
      color: rgba(255, 255, 255, 0.2);
      letter-spacing: 3px;
    }}
  </style>
</head>
<body>

  <!-- Root composition container with required structural attributes -->
  <div id="stage" data-composition-id="tiktok_video" data-start="0" data-width="1080" data-height="1920">
    <div class="aurora-bg"></div>
    <div class="vignette"></div>
    <div class="grain-overlay"></div>
    <div id="flash-overlay"></div>
    
    <!-- ==========================================
         SCENE 1: INTRO (0.00s - {s1_end:.2f}s)
         ========================================== -->
    <div id="s1" class="scene">
      <div class="scene-content">
        <div class="intro-header" id="s1-header"><span>{title[0]}</span>{title[1:]}</div>
        <h1 class="intro-title" id="s1-title">{intro_text}</h1>
      </div>
    </div>
    
    <!-- ==========================================
         SCENE 2: BUILDUP ({s2_start:.2f}s - {s2_end:.2f}s)
         ========================================== -->
    <div id="s2" class="scene">
      <div class="scene-content">
        <div class="buildup-box" id="s2-box">
          <h2 class="buildup-title">LOADING THE DROP...</h2>
          <div class="buildup-progress-container">
            <div class="buildup-progress-bar" id="s2-progress"></div>
          </div>
          <div class="buildup-info">TRACK BPM: {analysis['bpm']:.0f}</div>
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
          <div class="visualizer">
            <div class="bar s3-bar" id="s3-b1"></div>
            <div class="bar s3-bar" id="s3-b2"></div>
            <div class="bar s3-bar" id="s3-b3"></div>
            <div class="bar s3-bar" id="s3-b4"></div>
            <div class="bar s3-bar" id="s3-b5"></div>
            <div class="bar s3-bar" id="s3-b6"></div>
            <div class="bar s3-bar" id="s3-b7"></div>
          </div>
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
          <div class="visualizer">
            <div class="bar s4-bar" id="s4-b1"></div>
            <div class="bar s4-bar" id="s4-b2"></div>
            <div class="bar s4-bar" id="s4-b3"></div>
            <div class="bar s4-bar" id="s4-b4"></div>
            <div class="bar s4-bar" id="s4-b5"></div>
            <div class="bar s4-bar" id="s4-b6"></div>
            <div class="bar s4-bar" id="s4-b7"></div>
          </div>
        </div>
      </div>
    </div>
    
    <!-- ==========================================
         SCENE 5: OUTRO ({s5_start:.2f}s - {s5_end:.2f}s)
         ========================================== -->
    <div id="s5" class="scene">
      <div class="scene-content">
        <div class="outro-card" id="s5-card">
          <div class="outro-logo"><span>{title[0]}</span>{title[1:]}</div>
          <div class="outro-cta-text" id="s5-text">{outro_text}</div>
          <div class="outro-btn-container">
            <button class="outro-btn" id="s5-btn">Subscribe</button>
          </div>
          <div class="outro-footer">HYPERPIPELINE &bull; 2026</div>
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
    // Drift card/scale slowly
    tl.to("#s1 .scene-content", {{ scale: 1.03, duration: {s1_end - s1_start:.2f}, ease: "none" }}, 0.0);
    
    // --- SCENE 2 (Buildup) ---
    tl.to("#s2-box", {{ scale: 1.0, opacity: 1, duration: 0.8, ease: "power4.out" }}, {s2_start:.2f} + 0.1);
    // Animate progress bar to 100%
    tl.to("#s2-progress", {{ width: "100%", duration: {s2_end - s2_start:.2f} - 0.2, ease: "sine.inOut" }}, {s2_start:.2f} + 0.1);
    
    // --- SCENE 5 (Outro) ---
    tl.to("#s5-card", {{ y: 0, opacity: 1, duration: 1.2, ease: "power4.out" }}, {s5_start:.2f} + 0.2);
    // Pulse CTA follow button with a finite loop to satisfy lint requirements
    const outroDuration = {s5_end - s5_start:.2f};
    const followRepeat = Math.max(0, Math.floor(outroDuration / 1.2) - 1);
    tl.to("#s5-btn", {{ scale: 1.06, duration: 0.6, repeat: followRepeat, yoyo: true, ease: "sine.inOut" }}, {s5_start:.2f} + 0.8);
    // Slow fadeout at the end
    tl.to("#stage", {{ opacity: 0, duration: 1.5, ease: "power2.in" }}, {s5_end:.2f} - 1.5);
    
    // ==========================================
    // 3. BEAT-SYNCED MOTIONS
    // ==========================================
    
    musicData.beats.forEach((beat, index) => {{
      const t = beat.time;
      const energy = beat.energy;
      
      // A: Buildup pulse (Scene 2)
      if (t >= {s2_start:.4f} && t < {s2_end:.4f}) {{
        tl.to("#s2-box", {{ scale: 1.02 + (0.02 * energy), duration: 0.05, ease: "power1.out", overwrite: "auto" }}, t);
        tl.to("#s2-box", {{ scale: 1.0, duration: 0.15, ease: "power1.inOut", overwrite: "auto" }}, t + 0.05);
      }}
      
      // B: Climax Drop Act 1 (Scene 3)
      if (t >= {s3_start:.4f} && t < {s3_end:.4f}) {{
        // Flash overlay
        tl.fromTo("#flash-overlay", 
          {{ opacity: 0 }}, 
          {{ opacity: 0.18 * energy, duration: 0.03, yoyo: true, repeat: 1 }}, 
          t
        );
        
        // Stage Shake
        const shakeX = (seededRandom() - 0.5) * 30 * energy;
        const shakeY = (seededRandom() - 0.5) * 30 * energy;
        const shakeRot = (seededRandom() - 0.5) * 3 * energy;
        tl.to("#stage", {{ x: shakeX, y: shakeY, rotation: shakeRot, duration: 0.03, ease: "none", overwrite: "auto" }}, t);
        tl.to("#stage", {{ x: 0, y: 0, rotation: 0, duration: 0.07, ease: "power1.out", overwrite: "auto" }}, t + 0.03);
        
        // Equalizer Equal bounce
        tl.to(".s3-bar", {{ scaleY: () => 1 + (seededRandom() * 6 * energy), duration: 0.05, yoyo: true, repeat: 1, overwrite: "auto" }}, t);
        
        // Word Swap on beat
        const wordIndex = index % wordsS3.length;
        const activeWord = wordsS3[wordIndex];
        tl.call(() => {{
          const txtEl = document.getElementById("s3-text");
          if (txtEl) {{
            txtEl.innerHTML = activeWord;
            txtEl.className = (wordIndex % 2 === 0) ? "drop-word glow-active" : "drop-word";
          }}
        }}, null, t);
        
        // Word bounce scale
        tl.fromTo("#s3-text", 
          {{ scale: 0.85 }}, 
          {{ scale: 1.05, duration: 0.05, ease: "power2.out", yoyo: true, repeat: 1, overwrite: "auto" }}, 
          t
        );
      }}
      
      // C: Climax Drop Act 2 (Scene 4)
      if (t >= {s4_start:.4f} && t < {s4_end:.4f}) {{
        // Flash overlay (more energetic)
        tl.fromTo("#flash-overlay", 
          {{ opacity: 0 }}, 
          {{ opacity: 0.25 * energy, duration: 0.03, yoyo: true, repeat: 1 }}, 
          t
        );
        
        // Stage Shake (heavier)
        const shakeX = (seededRandom() - 0.5) * 45 * energy;
        const shakeY = (seededRandom() - 0.5) * 45 * energy;
        const shakeRot = (seededRandom() - 0.5) * 5 * energy;
        tl.to("#stage", {{ x: shakeX, y: shakeY, rotation: shakeRot, duration: 0.03, ease: "none", overwrite: "auto" }}, t);
        tl.to("#stage", {{ x: 0, y: 0, rotation: 0, duration: 0.07, ease: "power1.out", overwrite: "auto" }}, t + 0.03);
        
        // Equalizer bounce
        tl.to(".s4-bar", {{ scaleY: () => 1 + (seededRandom() * 8 * energy), duration: 0.05, yoyo: true, repeat: 1, overwrite: "auto" }}, t);
        
        // Word Swap on beat (S4 list)
        const wordIndex = index % wordsS4.length;
        const activeWord = wordsS4[wordIndex];
        tl.call(() => {{
          const txtEl = document.getElementById("s4-text");
          if (txtEl) {{
            txtEl.innerHTML = activeWord;
            txtEl.className = (wordIndex % 2 === 0) ? "drop-word glow-active" : "drop-word";
          }}
        }}, null, t);
        
        // Word bounce scale
        tl.fromTo("#s4-text", 
          {{ scale: 0.85 }}, 
          {{ scale: 1.08, duration: 0.05, ease: "power2.out", yoyo: true, repeat: 1, overwrite: "auto" }}, 
          t
        );
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
