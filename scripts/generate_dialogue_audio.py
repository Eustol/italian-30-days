#!/usr/bin/env python3
"""Generate two-voice Italian MP3 dialogues from the lessons in index.html."""

import argparse
import asyncio
import json
import re
import subprocess
import tempfile
from pathlib import Path

import edge_tts


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "index.html"
OUTPUT_DIR = ROOT / "audio" / "dialogues"
LEARNER_VOICE = "it-IT-IsabellaNeural"
PARTNER_VOICE = "it-IT-DiegoNeural"


def load_dialogues():
    html = HTML_PATH.read_text(encoding="utf-8")
    script = re.search(r"<script>([\s\S]*?)</script>", html).group(1)
    start = script.index("const p =")
    end = script.index("const lessonToolkits =")
    lesson_source = script[start:end]
    node_program = """
let source = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => source += chunk);
process.stdin.on('end', () => {
  const lessons = new Function(source + '\\nreturn lessons;')();
  process.stdout.write(JSON.stringify(lessons.map(lesson => ({
    title: lesson.title,
    dialogue: lesson.dialogue
  }))));
});
"""
    result = subprocess.run(
        ["node", "-e", node_program],
        input=lesson_source,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


async def synthesize_line(text, voice, rate, output_path):
    spoken_text = text.rstrip(".?!") + ". ..."
    last_error = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(
                spoken_text,
                voice,
                rate=rate,
                pitch="-2Hz" if voice == PARTNER_VOICE else "+0Hz",
            )
            await communicate.save(str(output_path))
            return
        except Exception as error:
            last_error = error
            await asyncio.sleep(1 + attempt)
    raise last_error


async def generate_day(day_number, lesson, semaphore):
    output_path = OUTPUT_DIR / f"day-{day_number:02d}.mp3"
    async with semaphore:
        with tempfile.TemporaryDirectory(prefix=f"italian-day-{day_number:02d}-") as temp_dir:
            chunks = []
            for line_number, (speaker, italian, _meaning) in enumerate(lesson["dialogue"], 1):
                voice = LEARNER_VOICE if speaker.startswith("你") else PARTNER_VOICE
                rate = "-18%" if voice == LEARNER_VOICE else "-12%"
                chunk = Path(temp_dir) / f"line-{line_number:02d}.mp3"
                await synthesize_line(italian, voice, rate, chunk)
                chunks.append(chunk.read_bytes())
            output_path.write_bytes(b"".join(chunks))
            print(f"day {day_number:02d}: {lesson['title']} -> {output_path.name}")


async def main(days):
    lessons = load_dialogues()
    if len(lessons) != 30:
        raise RuntimeError(f"Expected 30 lessons, found {len(lessons)}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    selected = set(days or range(1, 31))
    semaphore = asyncio.Semaphore(4)
    await asyncio.gather(*(
        generate_day(index, lesson, semaphore)
        for index, lesson in enumerate(lessons, 1)
        if index in selected
    ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("days", nargs="*", type=int, help="Optional lesson numbers")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.days))
