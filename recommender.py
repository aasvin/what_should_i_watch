#!/usr/bin/env python3
"""
Prime Video Content Recommender
Follows the guidelines defined in MakeRecommendations.md

Usage:
  python3 recommender.py              # rule-based (default, no API key needed)
  python3 recommender.py --llm        # Claude reads synopses + genres for richer matching
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# ── Paths ─────────────────────────────────────────────────────────────────────

JSON_PATH = Path(__file__).parent / "prime_video_content.json"

# ── Shared constants ──────────────────────────────────────────────────────────

MATURE_RATINGS = {"R", "NC-17", "TV-MA"}

VIOLENT_GENRES = {
    "gore", "violence", "horror", "slasher", "gory",
    "disturbing", "violent", "graphic", "brutal",
}

MODEL = "claude-sonnet-4-6"

# ── Rule-based constants ──────────────────────────────────────────────────────

# Preferred genres/moods per emotional state
# Guideline 2.1 → calm; 2.2 → cheer_up; 2.3 → energise; 2.4 → cerebral
EMOTION_GENRE_MAP: Dict[str, List[str]] = {
    "calm": [
        "Heartwarming", "Comedy", "Light", "Romantic",
        "Uplifting", "Feel-Good", "Soothing", "Family",
    ],
    "cheer_up": [
        "Comedy", "Heartwarming", "Funny", "Uplifting",
        "Lighthearted", "Feel-Good", "Quirky",
    ],
    "energise": [
        "Action", "Adventure", "Electrifying", "Bold",
        "Thriller", "Suspenseful", "Exciting", "Intense",
    ],
    "cerebral": [
        "Documentary", "Mystery", "Mysterious", "Drama",
        "Understated", "Thought-Provoking", "Cerebral",
    ],
}

# Maps free-text emotion to an internal category
EMOTION_CLASSIFIER: Dict[str, List[str]] = {
    "calm": [
        "angry", "frustrated", "annoyed", "mad",
        "irritated", "stressed", "anxious", "tense", "agitated",
    ],
    "cheer_up": [
        "depressed", "sad", "low", "down", "unhappy",
        "feeling low", "blue", "lonely", "miserable", "upset",
    ],
    "energise": [
        "bored", "low on energy", "tired", "lethargic",
        "restless", "meh", "blah", "sluggish", "flat",
    ],
    "cerebral": [
        "curious", "deep", "thoughtful", "intellectual",
        "introspective", "philosophical", "pensive", "reflective",
    ],
}


# ── Parsing helpers ───────────────────────────────────────────────────────────

def parse_duration_minutes(duration: str) -> Optional[int]:
    """Convert '2 h 9 min' or '1h' to total minutes. Returns None for TV episodes."""
    s = (duration or "").strip().lower()
    m = re.match(r"(\d+)\s*h\s*(\d+)\s*min", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"(\d+)\s*h$", s)
    if m:
        return int(m.group(1)) * 60
    return None


def parse_user_time(raw: str) -> Optional[int]:
    """Convert free-text time input to minutes."""
    s = raw.strip().lower()
    m = re.match(r"(\d+(?:\.\d+)?)\s*h(?:ou?r?s?)?\s*(?:and\s+)?(\d+)?\s*m(?:in(?:utes?)?)?", s)
    if m:
        return int(float(m.group(1)) * 60) + int(m.group(2) or 0)
    m = re.match(r"(\d+(?:\.\d+)?)\s*h(?:ou?r?s?)?$", s)
    if m:
        return int(float(m.group(1)) * 60)
    m = re.match(r"(\d+)\s*m(?:in(?:utes?)?)?$", s)
    if m:
        return int(m.group(1))
    m = re.match(r"(\d+)$", s)
    if m:
        return int(m.group(1))
    return None


# ── Hard filters (shared by both modes) ──────────────────────────────────────

def is_violent(item: dict) -> bool:
    genres_lower = {g.lower() for g in item.get("genres", [])}
    return bool(genres_lower & VIOLENT_GENRES)


def apply_hard_filters(catalogue: List[dict], available_min: int, after_9pm: bool) -> List[dict]:
    """
    Non-negotiable rules applied before any ranking. Same in both modes.

    Strict Rule 1 : After 9 PM — exclude violent/gory content.
    Strict Rule 3 : More than 3 hours available — movies only, no binge TV.
    Guideline 1   : Less than 30 minutes — TV shows only, no movies.
    """
    filtered = []
    for item in catalogue:
        item_type = item.get("type", "Movie")
        if after_9pm and is_violent(item):
            continue
        if available_min > 180 and item_type == "TV Show":
            continue
        if available_min < 30 and item_type == "Movie":
            continue
        filtered.append(item)
    return filtered


# ── Rule-based ranking ────────────────────────────────────────────────────────

def classify_emotion(raw: str) -> str:
    """Map free-text emotion to an internal category."""
    lower = raw.lower()
    for category, keywords in EMOTION_CLASSIFIER.items():
        if any(kw in lower for kw in keywords):
            return category
    return "cheer_up"  # neutral default — uplifting content


def score_item(item: dict, emotion_cat: str) -> float:
    """
    Score by genre tag overlap with the emotion's preferred moods.
    IMDb rating breaks ties within the same overlap tier.
    """
    preferred = {g.lower() for g in EMOTION_GENRE_MAP.get(emotion_cat, [])}
    genres_lower = {g.lower() for g in item.get("genres", [])}
    overlap = len(preferred & genres_lower)
    try:
        imdb = float(item.get("imdb_rating") or 0)
    except ValueError:
        imdb = 0.0
    return overlap * 10 + imdb


def rank_by_rules(candidates: List[dict], emotion_raw: str) -> List[dict]:
    """
    Rank candidates using genre-tag matching and IMDb rating.
    Returns a list of {"name": ..., "reason": ...} dicts in descending score order.
    """
    emotion_cat = classify_emotion(emotion_raw)
    preferred = EMOTION_GENRE_MAP.get(emotion_cat, [])

    scored = sorted(
        candidates,
        key=lambda item: score_item(item, emotion_cat),
        reverse=True,
    )

    results = []
    for item in scored:
        matching = [g for g in item.get("genres", []) if g in preferred]
        if matching:
            reason = f"Matched on: {', '.join(matching)}."
        else:
            reason = f"Highest rated available option (IMDb {item.get('imdb_rating', 'N/A')})."
        results.append({"name": item["name"], "reason": reason})

    return results


# ── LLM-based ranking ─────────────────────────────────────────────────────────

def rank_by_llm(candidates: List[dict], emotion_raw: str, available_min: int) -> List[dict]:
    """
    Send filtered candidates to Claude. Claude reads each title's synopsis AND
    genre tags to reason about mood fit, then returns a ranked list with
    natural-language explanations.

    Returns a list of {"name": ..., "reason": ...} dicts.
    """
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("The 'anthropic' package is not installed. Run: pip3 install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""You are a thoughtful content recommendation assistant for a streaming platform.

A user wants to watch something and has provided the following context:
- Time available: {available_min} minutes
- How they are feeling: "{emotion_raw}"

Here are the candidate titles available to them (already filtered by hard rules):

{json.dumps(candidates, indent=2)}

Your job is to rank the top 3 titles that best match how the user is feeling right now.

Guidelines for matching emotion to content:
- Angry / frustrated / annoyed → prioritise content that is calming, heartwarming, or relaxing
- Depressed / sad / feeling low → prioritise content that will make them smile or laugh
- Bored / low on energy → prioritise high-energy, action-packed, or exciting content
- Curious / in a thoughtful mood → prioritise cerebral, mysterious, or documentary-style content

Important: Do NOT rely only on genre tags. Read the synopsis of each title carefully.
A movie tagged "Drama" might still be the perfect pick for someone who is sad if the
synopsis reveals it is ultimately uplifting. Use your full understanding of both the
description and the genres together.

Respond with a JSON array (and nothing else) in this exact format:
[
  {{
    "name": "exact title name from the catalogue",
    "reason": "2-3 sentences explaining why this title fits the user's current mood, referencing specific details from the synopsis and/or genres"
  }},
  ...
]

Return only the JSON array. No preamble, no markdown fences."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return json.loads(response.content[0].text.strip())


# ── Display ───────────────────────────────────────────────────────────────────

def print_recommendation(item: dict, reason: str, duration_min: Optional[int]) -> None:
    print(f"\n  Title    : {item['name']} ({item.get('year', 'N/A')})")
    print(f"  Type     : {item.get('type', 'N/A')}")
    print(f"  Duration : {item.get('duration', 'N/A')}")
    print(f"  Rating   : {item.get('film_rating', 'NR')}")
    print(f"  IMDb     : {item.get('imdb_rating', 'N/A')} ({item.get('imdb_review_count', 'N/A')} ratings)")
    print(f"  Genres   : {', '.join(item.get('genres', []))}")

    desc = item.get("description", "")
    if desc:
        print(f"  Synopsis : {desc[:150]}{'...' if len(desc) > 150 else ''}")

    if duration_min and duration_min > 120:
        print("  [Note] This movie is over 2 hours — you may need more than one sitting to finish it.")

    print(f"  [Why] {reason}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Prime Video content recommender.")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Use Claude to rank by reading synopses and genres (requires ANTHROPIC_API_KEY).",
    )
    args = parser.parse_args()

    if not JSON_PATH.exists():
        print(f"Error: {JSON_PATH} not found. Run the scraper first.")
        return

    with open(JSON_PATH, encoding="utf-8") as f:
        catalogue: List[dict] = json.load(f)

    if not catalogue:
        print("The content catalogue is empty. Run the scraper to populate it.")
        return

    mode_label = "LLM-powered" if args.llm else "rule-based"
    print(f"\nPrime Video — Content Recommender ({mode_label})")
    print("=" * 40)

    # ── Question 1: Time available ────────────────────────────────────────────
    available_min: Optional[int] = None
    while not available_min or available_min <= 0:
        raw = input("\nHow much time do you have? (e.g. '2 hours', '45 minutes', '90'): ").strip()
        available_min = parse_user_time(raw)
        if not available_min or available_min <= 0:
            print("  I didn't catch that. Please try something like '2 hours' or '45 minutes'.")
            available_min = None

    # ── Question 2: Current emotion ───────────────────────────────────────────
    emotion_raw = input("\nHow are you feeling right now? ").strip() or "okay"

    # ── Time-of-day context ───────────────────────────────────────────────────
    now = datetime.now()
    after_9pm = now.hour >= 21

    print(f"\n  Current time    : {now.strftime('%I:%M %p')}")
    print(f"  Time available  : {available_min} minutes")
    print(f"  Feeling         : {emotion_raw}")
    if after_9pm:
        print("  [9 PM rule] Violent or gory content will be excluded.")

    # ── Step 1: Hard rule filtering (always applied) ──────────────────────────
    candidates = apply_hard_filters(catalogue, available_min, after_9pm)

    if not candidates:
        print("\n  No content matched your current situation. Try adjusting your available time.")
        return

    # ── Step 2: Rank candidates ───────────────────────────────────────────────
    if args.llm:
        print("\n  Asking Claude to find the best match for your mood...")
        try:
            ranked = rank_by_llm(candidates, emotion_raw, available_min)
        except Exception as e:
            print(f"\n  Could not reach Claude: {e}")
            return
    else:
        ranked = rank_by_rules(candidates, emotion_raw)

    # ── Step 3: Present recommendations (with mature content gate) ────────────
    catalogue_by_name = {item["name"]: item for item in catalogue}

    print(f"\n{'─' * 40}")
    print("  Recommendations for you:")

    shown = 0
    for rec in ranked:
        if shown >= 3:
            break

        name = rec.get("name", "")
        item = catalogue_by_name.get(name)
        if not item:
            continue  # LLM hallucinated a title not in the catalogue

        rating = (item.get("film_rating") or "").upper().strip()
        duration_min = parse_duration_minutes(item.get("duration", ""))

        # Strict Rule 2: mature content — confirm before showing
        if rating in MATURE_RATINGS:
            answer = input(
                f"\n  '{name}' is rated {rating} (mature audiences). "
                "There may be kids in the room — OK to suggest this title? (yes/no): "
            ).strip().lower()
            if answer not in ("yes", "y"):
                continue

        print_recommendation(item, rec.get("reason", ""), duration_min)
        shown += 1
        print()

    if shown == 0:
        print("\n  All suitable titles were declined or unavailable. Nothing to recommend.")

    print("─" * 40)
    print("  Enjoy your viewing!\n")


if __name__ == "__main__":
    main()
