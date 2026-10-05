"""Generate original synthetic speech for installation checks; NOT singing validation.

Requires espeakng-loader (pip install espeakng-loader). Creates a repeated chorus
with an omitted verse in the supplied lyrics, without modifying any user MP3.
"""

import ctypes
import subprocess
import sys
import wave
from pathlib import Path

import espeakng_loader
import numpy as np


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(parents=True, exist_ok=True)
    lib = ctypes.CDLL(espeakng_loader.get_library_path())
    lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    sample_rate = lib.espeak_Initialize(2, 0, espeakng_loader.get_data_path().encode(), 0)
    if sample_rate <= 0:
        raise RuntimeError("eSpeak initialization failed")
    chunks = []
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p
    )

    @callback_type
    def collect(pointer, count, events):
        if count > 0 and pointer:
            chunks.append(np.ctypeslib.as_array(pointer, shape=(count,)).copy())
        return 0

    lib.espeak_SetSynthCallback(collect)
    lib.espeak_SetVoiceByName(b"en-us")
    lib.espeak_SetParameter(1, 135, 0)  # words per minute
    line = "Silver rivers carry morning light."
    encoded = (line + "\0").encode()
    lib.espeak_Synth.argtypes = [
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.espeak_Synth(encoded, len(encoded), 0, 1, 0, 1, None, None)
    lib.espeak_Synchronize()
    speech = np.concatenate(chunks)
    silence = np.zeros(sample_rate, dtype=np.int16)
    # No intro: first chorus begins at zero; missing verse then another chorus.
    audio = np.concatenate([speech, silence, speech, silence])
    wav_path = directory / "synthetic.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(audio.astype("<i2").tobytes())
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(wav_path),
            "-c:a",
            "libmp3lame",
            "-q:a",
            "3",
            str(directory / "synthetic.mp3"),
        ],
        check=True,
    )
    (directory / "lyrics.txt").write_text(
        line + "\nThis entire verse was removed from the recording.\n" + line + "\n",
        encoding="utf-8",
    )
    print(f"Generated {directory}; repeated phrase offset {len(speech) / sample_rate + 1:.3f} s")


if __name__ == "__main__":
    main()
