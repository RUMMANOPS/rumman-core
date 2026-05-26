# telegram_sync_state

## Purpose

`telegram_sync_state` stores one row per Telegram chat.

It is the checkpoint controller for Telegram ingestion.

## Why It Exists

Without this table, RUMMAN would not know:

- which chats were seen
- the newest message captured
- the oldest historical message reached
- whether backfill is completed
- how many messages were observed per chat

## Core Principle

Never crawl Telegram blindly.

Always use chat-level checkpoints.

## Key Fields

- platform_chat_id: stable Telegram chat identifier
- chat_type: private, group, supergroup, or channel
- chat_name: human-readable chat name
- newest_message_id: latest live message captured
- oldest_message_id: oldest historical message reached
- total_messages_seen: incremental count of seen messages
- backfill_completed: whether historical ingestion is finished

## Current Usage

The live listener updates:

- newest_message_id
- total_messages_seen
- chat_type
- chat_name

## Future Usage

Backfill workers should update:

- oldest_message_id
- backfill_completed
- checkpoint lag metrics

## Operational Rule

This table must remain lightweight and fast to update.
