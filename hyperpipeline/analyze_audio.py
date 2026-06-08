#!/usr/bin/env python3
import os
import json
import argparse
import numpy as np
import librosa

def analyze_audio(audio_path, output_path):
    print(f"Analyzing audio file: {audio_path}...")
    
    if not os.path.exists(audio_path):
        print(f"Error: Audio file {audio_path} not found.")
        return False
        
    # Load audio file (monophonic)
    y, sr = librosa.load(audio_path, sr=None)
    duration = librosa.get_duration(y=y, sr=sr)
    print(f"Loaded audio: {duration:.2f} seconds at {sr}Hz.")
    
    # 1. Estimate tempo (BPM) and beat positions
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    # tempo can be returned as an array or a single float
    if isinstance(tempo, np.ndarray):
        tempo = float(tempo[0])
    else:
        tempo = float(tempo)
        
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    print(f"Estimated Tempo: {tempo:.2f} BPM. Found {len(beat_times)} beats.")
    
    # 2. Compute Root Mean Square (RMS) energy per frame
    rms = librosa.feature.rms(y=y)[0]
    frames = range(len(rms))
    frame_times = librosa.frames_to_time(frames, sr=sr)
    
    # 3. Detect the "Drop" (Climax)
    # The drop is characterized by a rapid, large increase in energy.
    # We smooth the RMS energy curve to avoid noise and look for the maximum derivative.
    window_len = int(sr * 1.5 / 512)  # ~1.5 seconds smoothing window
    if window_len % 2 == 0:
        window_len += 1
    
    smooth_rms = np.convolve(rms, np.ones(window_len)/window_len, mode='same')
    
    # Find derivative (energy gradient)
    energy_gradient = np.diff(smooth_rms)
    gradient_times = frame_times[:-1]
    
    # To find a meaningful drop, we look for the maximum gradient after the first 5 seconds
    min_time_for_drop = 5.0
    valid_indices = np.where(gradient_times >= min_time_for_drop)[0]
    
    if len(valid_indices) > 0:
        max_grad_idx = valid_indices[np.argmax(energy_gradient[valid_indices])]
        drop_time = float(gradient_times[max_grad_idx])
    else:
        # Fallback to global max gradient if track is shorter than 5 seconds
        max_grad_idx = np.argmax(energy_gradient)
        drop_time = float(gradient_times[max_grad_idx])
        
    print(f"Detected Drop Time: {drop_time:.2f} seconds.")
    
    # 4. Construct Segments (Intro, Buildup, Drop, Outro)
    # A typical social media buildup is about 6-8 seconds before the drop.
    buildup_duration = min(8.0, drop_time)
    intro_end = drop_time - buildup_duration
    
    # Drop segment is usually 20-30 seconds of high-energy climax
    drop_duration = min(30.0, duration - drop_time)
    outro_start = drop_time + drop_duration
    
    segments = [
        {"name": "intro", "start": 0.0, "end": float(intro_end)},
        {"name": "buildup", "start": float(intro_end), "end": float(drop_time)},
        {"name": "drop", "start": float(drop_time), "end": float(outro_start)},
        {"name": "outro", "start": float(outro_start), "end": float(duration)}
    ]
    
    # 5. Extract localized beat energy
    # We want to know how strong each beat is so the visual animation can scale proportionally
    beat_energies = []
    for beat_t in beat_times:
        # Find closest RMS frame index
        closest_frame = np.argmin(np.abs(frame_times - beat_t))
        beat_energies.append(float(rms[closest_frame]))
        
    # Normalize beat energies between 0.2 and 1.0 for aesthetic scaling
    if len(beat_energies) > 0:
        min_e, max_e = min(beat_energies), max(beat_energies)
        if max_e > min_e:
            norm_energies = [0.2 + 0.8 * (e - min_e) / (max_e - min_e) for e in beat_energies]
        else:
            norm_energies = [1.0] * len(beat_energies)
    else:
        norm_energies = []
        
    beats_with_energy = [
        {"time": float(t), "energy": float(e)}
        for t, e in zip(beat_times, norm_energies)
    ]
    
    # Save results
    analysis = {
        "bpm": float(tempo),
        "duration": float(duration),
        "drop_time": float(drop_time),
        "segments": segments,
        "beats": beats_with_energy
    }
    
    parent_dir = os.path.dirname(output_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)
        
    with open(output_path, "w") as f:
        json.dump(analysis, f, indent=4)
        
    print(f"Analysis saved to: {output_path}")
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Analyze audio to detect beats and drops using Librosa")
    parser.add_argument('--audio', default="assets/audio.mp3", help="Input MP3 audio file")
    parser.add_argument('--output', default="assets/analysis.json", help="Output JSON analysis path")
    args = parser.parse_args()
    
    analyze_audio(args.audio, args.output)
