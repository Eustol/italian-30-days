#!/usr/bin/env python3
"""Generate neural Italian audio assets from the lessons in index.html."""

import argparse
import asyncio
import collections
import json
import re
import subprocess
import tempfile
from pathlib import Path

import edge_tts


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "index.html"
DIALOGUE_OUTPUT_DIR = ROOT / "audio" / "dialogues"
SPEECH_OUTPUT_DIR = ROOT / "audio" / "speech"
LEARNER_VOICE = "it-IT-IsabellaNeural"
PARTNER_VOICE = "it-IT-DiegoNeural"


def load_course():
    html = HTML_PATH.read_text(encoding="utf-8")
    script = re.search(r"<script>([\s\S]*?)</script>", html).group(1)
    start = script.index("const p =")
    end = script.index("const alphabet =")
    lesson_source = script[start:end]
    node_program = """
let source = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => source += chunk);
process.stdin.on('end', () => {
  const course = new Function(source + '\\nreturn { lessons, lessonToolkits };')();
  process.stdout.write(JSON.stringify(course));
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


def speech_hash(text):
    value = 2166136261
    for character in text:
        value ^= ord(character)
        value = (value * 16777619) & 0xFFFFFFFF
    return f"{value:08x}"


def collect_speech_texts(course):
    texts = []
    for lesson in course["lessons"]:
        texts.extend(phrase["it"] for phrase in lesson["phrases"])
    for toolkit in course["lessonToolkits"]:
        for _group_name, items in toolkit["groups"]:
            texts.extend(item["it"] for item in items)
    by_hash = collections.defaultdict(set)
    for text in texts:
        by_hash[speech_hash(text)].add(text)
    collisions = {key: values for key, values in by_hash.items() if len(values) > 1}
    if collisions:
        raise RuntimeError(f"Speech hash collision: {collisions}")
    return sorted(set(texts))


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
    output_path = DIALOGUE_OUTPUT_DIR / f"day-{day_number:02d}.mp3"
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


async def generate_speech_clip(text, semaphore):
    output_path = SPEECH_OUTPUT_DIR / f"{speech_hash(text)}.mp3"
    if output_path.exists() and output_path.stat().st_size > 1000:
        return
    async with semaphore:
        await synthesize_line(text, LEARNER_VOICE, "-14%", output_path)
        print(f"clip {output_path.stem}: {text}")


async def main(days, clips_only):
    course = load_course()
    lessons = course["lessons"]
    if len(lessons) != 30:
        raise RuntimeError(f"Expected 30 lessons, found {len(lessons)}")
    semaphore = asyncio.Semaphore(6)
    if not clips_only:
        DIALOGUE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        selected = set(days or range(1, 31))
        await asyncio.gather(*(
            generate_day(index, lesson, semaphore)
            for index, lesson in enumerate(lessons, 1)
            if index in selected
        ))
    speech_texts = collect_speech_texts(course)
    SPEECH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Generating {len(speech_texts)} unique core/toolkit clips")
    await asyncio.gather(*(generate_speech_clip(text, semaphore) for text in speech_texts))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("days", nargs="*", type=int, help="Optional lesson numbers")
    parser.add_argument("--clips-only", action="store_true", help="Only generate core/toolkit clips")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.days, arguments.clips_only))
