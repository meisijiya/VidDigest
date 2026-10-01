---
status: accepted
date: 2026-10-01
---

# 社区视频使用独立的全局表，不复用个人解析历史

解析历史按 `(user_id, video_url)` 去重，天然是「谁解析过什么」的个人记录，每用户只保留最近 30 条。
但社区要求同一个视频全站只有一份、可被所有人读——这两个语义对不上。
因此新增一张全局 `videos` 表，以 `video_url` 全局唯一，承载社区内容；`parse_history` 保持原样，仅作个人访问记录。

## Consequences

两张表职责分离：`videos` 不做条数裁剪，`parse_history` 的 30 条滚动删除规则保持不变。
所有「视频是否已存在」「取某视频的字幕」这类判断改为查 `videos`，不再查 `parse_history`。
注意现有唯一索引 `idx_history_user_url` 含 `user_id`，无法直接复用，需要新建全局唯一索引。
