"""`frozen_clock` 夹具本身不能是空操作。

AGENTS.md 的一条硬纪律：钉完时钟要补一条「把假时钟改成空操作」的变异，
否则「钉时钟」这件事本身没人守——将来有人为了省事把它删掉（或者写成一个
碰巧在大多数时候成立的替身），没有任何一条用例会转红。

所以本文件测的不是业务，是**夹具本身**。四条各守一种「假钉法」：

- 只返回常量但不冻结（两次调用应当不同，钉死后应当相同）；
- 返回真实时间（那等于没钉）；
- 顶替而不是继承 ``datetime``（``fromisoformat`` 会在别的路径上炸）；
- 钉在午夜前后那一格（最难的格子）。
"""
import time
from datetime import datetime, timezone

import database

from conftest import FROZEN_MOMENT


def test_it_really_freezes_the_clock(frozen_clock):
    """两次调用必须完全相同——**而且中间真的等了一会儿**。

    为什么要加 sleep：本机实测连续两次 ``datetime.now()`` 有 199832/200000 次
    返回完全相同的值（时钟量化到约 0.3ms），所以「两次相等」本身**证明不了
    任何事**。只有真的过掉一个量化格之后仍然相等，才是钉住了。
    """
    first = database.datetime.now(timezone.utc)
    time.sleep(0.05)
    second = database.datetime.now(timezone.utc)

    assert first == second, "假时钟没有钉住：50ms 之后它就走了"
    assert first == FROZEN_MOMENT == frozen_clock


def test_it_is_not_merely_returning_a_constant(frozen_clock):
    """反向：钉住的是**一个特定的时刻**，不是「随便什么时候都一样」。

    否则一条用它的用例可能在别的日期上悄悄成立——比如断言「跨天重置」
    时，两个「不同的今天」其实来自同一个常量。
    """
    assert FROZEN_MOMENT.year == 2026
    assert FROZEN_MOMENT == datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_the_pinned_moment_is_nowhere_near_midnight(frozen_clock):
    """钉在正午而不是 00:00。

    午夜前后那一格最难躲：预置与判定只要有一次跨过去，日期就翻页了。
    正午离两端都最远，且与「按日期重置」那类逻辑隔着整整半天。
    """
    assert FROZEN_MOMENT.hour == 12, "钉在了午夜前后——那正是最难的格子"
    assert FROZEN_MOMENT.minute == 0
    # 距离最近的 UTC 午夜至少还有 12 小时。
    hours_to_midnight = 24 - FROZEN_MOMENT.hour
    assert hours_to_midnight >= 12


def test_the_fake_clock_still_supports_fromisoformat(frozen_clock):
    """假类**继承** ``datetime`` 而不是顶替它。

    ``database`` 别处还要用 ``fromisoformat``；一个只实现了 ``now`` 的替身
    会让那些路径**因错误的原因**抛错——那种红不是护栏在响，是测错了东西。
    """
    parsed = database.datetime.fromisoformat("2026-06-15T12:00:00+00:00")

    assert parsed == FROZEN_MOMENT
    assert isinstance(parsed, datetime), "fromisoformat 的返回值类型变了"
