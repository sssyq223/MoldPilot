import sqlite3
import gzip
import os
import re

# 连接数据库
db_path = r'D:\PiDeck\resources\xueprompts.db'
output_dir = r'C:\Users\86187\.claude\skills\xueprompts'

# 创建输出目录
os.makedirs(output_dir, exist_ok=True)

# 连接数据库
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 关键词过滤：编程、规划、代理相关
keywords = [
    'plan', 'executing', 'agent', 'code', 'debug', 'refactor',
    'architect', 'developer', 'programming', 'skill', 'workflow'
]

# 查询编程类和重要的提示词
cursor.execute("""
    SELECT slug, title, content, category, description
    FROM xueprompts
    WHERE category IN ('编程提示词', '商业提示词', '办公提示词')
    OR slug LIKE '%plan%'
    OR slug LIKE '%executing%'
    OR slug LIKE '%agent%'
    ORDER BY category, slug
    LIMIT 100
""")

count = 0
for row in cursor.fetchall():
    slug, title, content_blob, category, desc_blob = row

    try:
        # 解压内容
        if content_blob:
            content = gzip.decompress(content_blob).decode('utf-8')
        else:
            content = ""

        if desc_blob:
            description = gzip.decompress(desc_blob).decode('utf-8')
        else:
            description = ""

        # 创建技能文件
        skill_content = f"""---
description: {title}
skill_name: {slug}
category: {category}
---

{description}

{content}
"""

        # 保存文件
        filename = f"{slug}.md"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(skill_content)

        count += 1
        print(f"[OK] {count}. {title}")

    except Exception as e:
        print(f"[SKIP] {slug}: {e}")

conn.close()
print(f"\n导出完成！共导出 {count} 个提示词到 {output_dir}")
