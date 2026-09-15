"""Run from project root: python scripts/model_probe.py (no secret output)."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.config import settings
from app.model_adapter import ModelError
from app.agent_worker import create_model


def main():
    config = settings()
    model = create_model(config.model_copy(update={'llm_max_output_tokens':128}))
    started = time.monotonic()
    try:
        message = model.generate([{'role': 'user', 'content': '请简短回复：模型连接成功。'}], [])
        print(json.dumps({'ok': True, 'model': config.active_model,
                          'seconds': round(time.monotonic()-started, 2),
                          'reply': message.get('content')}, ensure_ascii=False))
    except ModelError as error:
        print(json.dumps({'ok': False, 'code': str(error)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__': main()
