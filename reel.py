import os
import subprocess

from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs


# ============================================================
# SETTINGS
# ============================================================

load_dotenv()

API_KEY = os.getenv("ELEVENLABS_API_KEY")

if not API_KEY:
    raise ValueError(
        "ELEVENLABS_API_KEY was not found in .env"
    )


# ------------------------------------------------------------
# غيري Voice ID للصوت اللي اخترتيه من ElevenLabs
# ------------------------------------------------------------

VOICE_ID = "PUT_YOUR_VOICE_ID_HERE"


# ------------------------------------------------------------
# الفيديو اللي نزلتيه من Google Flow
# ------------------------------------------------------------

INPUT_VIDEO = "flow_reel.mp4"

VOICE_OUTPUT = "voiceover.mp3"

FINAL_VIDEO = "final_reel_with_voice.mp4"


# ============================================================
# AD SCRIPT
# ============================================================

TEXT = """
تعب وإرهاق؟

اكتشفي الحديد الليبوسومال بامتصاص عالي،
وتركيبة متكاملة من الفيتامينات.

يذوب مباشرة في الفم...
بطعم الكولا، وبدون سكر.

طاقتك تبدأ من الداخل.
"""


# ============================================================
# ELEVENLABS
# ============================================================

def generate_voice():

    print("Generating Arabic voice with ElevenLabs...")

    client = ElevenLabs(
        api_key=API_KEY
    )

    audio = client.text_to_speech.convert(
        voice_id=VOICE_ID,

        text=TEXT,

        model_id="eleven_multilingual_v2",

        output_format="mp3_44100_128",
    )

    with open(VOICE_OUTPUT, "wb") as f:

        for chunk in audio:
            f.write(chunk)

    print(f"Voice saved: {VOICE_OUTPUT}")


# ============================================================
# ADD VOICE TO VIDEO
# ============================================================

def add_voice_to_video():

    print("Adding voice to video...")

    if not os.path.exists(INPUT_VIDEO):
        raise FileNotFoundError(
            f"Video not found: {INPUT_VIDEO}"
        )

    if not os.path.exists(VOICE_OUTPUT):
        raise FileNotFoundError(
            f"Voice not found: {VOICE_OUTPUT}"
        )

    cmd = [
        "ffmpeg",
        "-y",

        # Video
        "-i",
        INPUT_VIDEO,

        # Voice
        "-i",
        VOICE_OUTPUT,

        # Keep video
        "-map",
        "0:v:0",

        # Use ElevenLabs voice
        "-map",
        "1:a:0",

        # Don't re-encode video
        "-c:v",
        "copy",

        # Audio codec
        "-c:a",
        "aac",

        "-b:a",
        "192k",

        # End when video ends
        "-shortest",

        FINAL_VIDEO,
    ]

    subprocess.run(
        cmd,
        check=True
    )

    print("\nDONE!")
    print(
        "Final video:",
        os.path.abspath(FINAL_VIDEO)
    )


# ============================================================
# MAIN
# ============================================================

def main():

    generate_voice()

    add_voice_to_video()


if __name__ == "__main__":
    main()
