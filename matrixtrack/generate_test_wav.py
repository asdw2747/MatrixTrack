import wave
import math
import struct

def create_51_test_file(filename="test_surround_mix.wav", duration_seconds=5.0):
    sample_rate = 44100
    num_channels = 6  # 5.1 Surround Setup
    num_frames = int(sample_rate * duration_seconds)
    
    # Open a real 16-bit PCM wave write file handle
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(2) # 16-bit PCM
        wf.setframerate(sample_rate)
        
        packed_frames = []
        for f in range(num_frames):
            t = f / sample_rate
            
            # Synthesize separate panning frequencies for different channels
            fl  = math.sin(2 * math.pi * 220 * t)  # Low continuous tone (Front Left)
            fr  = math.sin(2 * math.pi * 440 * t)  # High continuous tone (Front Right)
            c   = math.sin(2 * math.pi * 330 * t) if (int(t) % 2 == 0) else 0.0 # Pulsing Center Vocals
            lfe = math.sin(2 * math.pi * 60 * t) * 0.5  # Subwoofer low hum
            sl  = math.sin(2 * math.pi * 550 * t) * 0.3 # Quiet rear channel
            sr  = -sl  # PERFECTLY OUT-OF-PHASE SURROUND! (Should drive phase needle left)
            
            # Scale to 16-bit signed short boundaries
            ch_data = [int(v * 32767) for v in [fl, fr, c, lfe, sl, sr]]
            
            # Pack sequentially as interleaved binary string bytes
            packed_frames.extend(ch_data)
            
        # Write flat interleaved binary buffer to disk
        binary_data = struct.pack(f"<{len(packed_frames)}h", *packed_frames)
        wf.writeframes(binary_data)
    print(f"Successfully generated real 5.1 validation track: {filename}")

if __name__ == "__main__":
    create_51_test_file()
