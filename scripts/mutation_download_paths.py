"""工单 #53 的变异 harness：证明错误路径与 except 次序真被钉住了。

四条变异必须 KILLED，一条反向对照必须保持绿。

M1 与 M2 是本单的核心：`except HTTPException: raise` 那两行删掉（工单 #53 的由来），
以及把两段 except 的**次序**对调。两者都会让 500 变成 400。
"""
import io
import subprocess
import sys

from venv_python import venv_python

PY = venv_python()
TARGET = "backend/tests/test_download_video_error_paths.py"
MAIN = "backend/main.py"


def read(path):
    with io.open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def run_tests():
    proc = subprocess.run(
        [PY, "-m", "pytest", TARGET, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


RAISE_BLOCK = (
    "    except HTTPException:\n"
    "        raise\n"
    "    except Exception as e:\n"
)
RAISE_ONLY = (
    "    except HTTPException:\n"
    "        raise\n"
)
EXC_FIRST = (
    "    except Exception as e:\n"
)
SWAPPED = (
    "    except Exception as e:\n"
    '        raise HTTPException(status_code=400, detail={\n'
    '            "success": False,\n'
    '            "error": f"下载失败: {str(e)}"\n'
    "        })\n"
    "    except HTTPException:\n"
    "        raise\n"
)
SWAP_ANCHOR = RAISE_BLOCK + (
    '        raise HTTPException(status_code=400, detail={\n'
    '            "success": False,\n'
    '            "error": f"下载失败: {str(e)}"\n'
    "        })\n"
)

MUTATIONS = [
    {
        "id": "M1", "expect": "KILLED", "hits": 1,
        "why": "删掉 except HTTPException: raise（工单 #53 的由来）",
        "find": RAISE_BLOCK, "replace": EXC_FIRST,
    },
    {
        "id": "M2", "expect": "KILLED", "hits": 1,
        "why": "把两段 except 的次序对调：HTTPException 被 except Exception 吞掉",
        "find": SWAP_ANCHOR, "replace": SWAPPED,
    },
    {
        "id": "M3", "expect": "KILLED", "hits": 1,
        "why": "改 500 那条的文案",
        "find": 'raise HTTPException(status_code=500, detail="下载的文件不存在")',
        "replace": 'raise HTTPException(status_code=500, detail="下载失败")',
    },
    {
        "id": "M4", "expect": "KILLED", "hits": 1,
        "why": "改 parse 错误映射的前缀文案",
        "find": '"error": f"解析失败: {str(e)}"',
        "replace": '"error": f"出错了: {str(e)}"',
    },
    {
        "id": "R1", "expect": "GREEN", "hits": 1,
        "why": "反向对照：健康检查返回串与这两条路由无关",
        "find": '    return {"status": "ok"}',
        "replace": '    return {"status": "healthy"}',
    },
]

bad = 0
src = read(MAIN)
for m in MUTATIONS:
    for key in ("find", "replace", "hits", "expect"):
        if key not in m:
            print(f"[HARNESS ERROR] {m.get('id')} 缺 {key}——先修 harness，不要动断言")
            bad += 1
            break
    else:
        hits = src.count(m["find"])
        if hits != m["hits"]:
            print(f"[PRECHECK BAD] {m['id']} anchor 命中 {hits} 次，预期 {m['hits']}"
                  f"——先问「是文件的问题还是我锚点的问题」")
            bad += 1
            continue
        write(MAIN, src.replace(m["find"], m["replace"], 1))
        code, out = run_tests()
        write(MAIN, src)

        verdict = "KILLED" if code != 0 else "GREEN"
        has_assertion = "AssertionError" in out
        ok = verdict == m["expect"] and (verdict != "KILLED" or has_assertion)
        if not ok:
            bad += 1
        print(f"[{'OK ' if ok else 'BAD'}] {m['id']} {verdict} (期望 {m['expect']})"
              f"  AssertionError={'有' if has_assertion else '无'}  exit={code}"
              f"  — {m['why']}")
        if not ok:
            print(out[-700:])

print(f"\n收尾自查：{bad} 条异常")
sys.exit(1 if bad else 0)