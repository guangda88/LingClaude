#!/usr/bin/env python3
"""Count voting rounds in lingmessage threads."""

import json
from pathlib import Path

THREADS_DIR = Path.home() / ".lingmessage" / "threads"
VOTE_KEYWORDS = ["vote", "投票", "round", "轮次", "approval", "批准", "reject", "拒绝"]

proposal_threads = {}

# Find all threads with PRO- in topic
for thread_file in THREADS_DIR.glob("*/thread.json"):
    try:
        with open(thread_file) as f:
            thread = json.load(f)

        topic = thread.get("topic", "")
        if "PRO-" in topic:
            # Extract proposal number
            import re
            match = re.search(r"PRO-(\d+)", topic)
            if match:
                prop_id = f"PRO-{match.group(1).zfill(3)}"
                proposal_threads[prop_id] = {
                    "thread_id": thread["thread_id"],
                    "topic": topic,
                    "participants": thread.get("participants", []),
                    "message_count": thread.get("message_count", 0),
                }
    except Exception as e:
        print(f"Error reading {thread_file}: {e}")

print(f"Found {len(proposal_threads)} proposal threads")
print()

# Analyze each thread for voting rounds
for prop_id, info in sorted(proposal_threads.items()):
    thread_dir = THREADS_DIR / info["thread_id"]
    vote_messages = []

    for msg_file in thread_dir.glob("*.json"):
        if msg_file.name == "thread.json":
            continue

        try:
            with open(msg_file) as f:
                msg = json.load(f)

            body = msg.get("body", "").lower()
            subject = msg.get("subject", "").lower()

            # Check for voting keywords
            if any(keyword.lower() in body or keyword.lower() in subject
                   for keyword in VOTE_KEYWORDS):
                vote_messages.append({
                    "message_id": msg.get("message_id"),
                    "sender": msg.get("sender"),
                    "message_type": msg.get("message_type"),
                    "subject": msg.get("subject"),
                    "timestamp": msg.get("timestamp"),
                })
        except Exception as e:
            pass

    print(f"{prop_id}: {len(vote_messages)} voting-related messages")
    if vote_messages:
        print(f"  Topic: {info['topic']}")
        print(f"  Participants: {info['participants']}")
        for i, vm in enumerate(vote_messages[:3]):  # Show first 3
            print(f"  [{i+1}] {vm['sender']}: {vm['message_type']} - {vm['subject']}")
        if len(vote_messages) > 3:
            print(f"  ... and {len(vote_messages) - 3} more")
    print()
