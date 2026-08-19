---
name: work-log-mine
description: >
  Launch the work-log-miner agent to walk OneNote work-log sections day by day
  and 3D-tag each date page's direct children: kind, home notebook/section,
  and domain. Use when the user wants to mine 工作日志, classify 经验/见解,
  scan Q2/Q3 logs, or runs /work-log-mine.
argument-hint: "[Q2|Q3|section name] [optional dates]"
---

# Mine work logs

Read `.grok/agents/work-log-miner.md` and follow it. Prefer spawning `subagent_type: work-log-miner` with the user's scope in the prompt. Do **not** pass `capability_mode: read-only` — that strips `search_tool` / `use_tool`. Omit `capability_mode`, or use `all`. If that type is unavailable in this session, execute the agent file yourself.

## Scope to pass

- Default notebook: `我的笔记本`
- Default sections: `工作日志-2026 Q2` and/or `工作日志-2026 Q3` as the user named them
- Optional date filter from the slash arguments (for example `/work-log-mine Q3 7.01-7.15`)

## Classify only vs staging move

Default spawn: classify only. Do not put `copy` / `move` or a staging notebook in the prompt.

When the user asks to drop pages into a temporary notebook, spawn **one miner per date page** and give all three:

1. Verb: `copy` or `move` (do not upgrade copy to move)
2. Staging `notebook_id`
3. `home → destination_section_id` map

Optional: 「可建区」only for that staging notebook. Parent should create the staging notebook and sections first when possible.

Do not invent extra sections in the real knowledge books. Do not pass real Portal/查阅型/开发 IDs unless the user named those exact destinations.