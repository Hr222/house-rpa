# -*- coding: utf-8 -*-
"""可比市场算法：以目标小区成交为锚、报价频率峰与周边小区为辅助的取值决策。

与老算法（weighted_median.WeightedMedianAlgorithm，注册名 WEIGHTED_MEDIAN）
平行存在、互不感知；本模块的实例注册为 DEFAULT，是服务链路的默认算法。
纯函数模块：无 IO、无浏览器依赖，输入装配由编排层（app/inquiry/）完成。

数据前提（字段口径详见 models.AlgorithmInput / NearbyCommunityInput）：
- quote_price_lists：目标小区挂牌单价，已按请求面积 ±1㎡ 严格筛选并去重；
- deal_samples：目标小区成交单价，已跨平台去重并按请求面积 ±10% + 近半年
  筛选（由本模块的 prepare_deal_samples 在编排层调用产出）；
- nearby_communities：周边小区（community_data.find_nearby_communities 按
  "距离分带 → 年份差 → 距离"取前 3），数据口径与目标小区一致。

四个业务场景（与需求文档的 case 编号一一对应）：
- case1  目标成交样本 ≥3：直接取成交均价（样本充足，成交即市场价）；
- case2  0 < 样本 < 3：报价频率最高价 P 与成交均值差距 ≤15% 取 P（信报价），
         否则取成交均值 ×110%（信成交，上浮折算回挂牌口径）；
- case3  样本 = 0 且周边有成交：周边成交均值 D_near 与 P 比较，
         D_near > P 取 D_near ×110%，否则取 P；
- case4  目标与周边均无成交：取各小区报价频率最高价的平均值。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Optional

from app.algorithm.area_rules import relative_area_bounds
from app.algorithm.models import (
    AlgorithmEvaluation,
    AlgorithmInput,
    Decision,
    PriceCandidate,
)

# ===== 决策常量（业务口径，修改前先与需求方确认） =====
# case1 门槛：目标小区可比成交样本数达到该值即认为"成交样本充足"。
DEAL_SAMPLE_MIN_COUNT = 3
# 报价频率最高价 P 与成交均值的允许差距（分母取两者中较小值，
# 等价于"无论以谁为分母差距都在该比例内"）。
PRICE_GAP_TOLERANCE = 0.15
# "以成交为锚"分支的统一上浮系数：成交价 ×110% 折算回挂牌口径的估值。
DEAL_ANCHOR_MARKUP = 1.10
# 成交样本的日期窗口（月）；与 deal_screening 的"近半年"口径一致。
DEAL_MONTHS_WINDOW = 6


# ===== 分支码（模块级字符串常量，与老算法的裸字符串风格保持一致） =====
# 文案统一登记在 branch_text.BRANCH_TEXT，未登记的分支出口自动回退为分支码。
BRANCH_DEAL_AVG = "MARKET_DEAL_AVG"                     # case1
BRANCH_LISTING_CONSISTENT = "MARKET_LISTING_CONSISTENT"  # case2：差距≤15% 取报价
BRANCH_DEAL_ANCHORED = "MARKET_DEAL_ANCHORED"            # case2：取成交×110%
BRANCH_NEARBY_DEAL_ANCHORED = "MARKET_NEARBY_DEAL_ANCHORED"  # case3.1
BRANCH_NEARBY_DEAL_LOWER = "MARKET_NEARBY_DEAL_LOWER"        # case3.2
BRANCH_LISTING_AVERAGE = "MARKET_LISTING_AVERAGE"            # case4


def _cutoff_str(months: int, *, now: Optional[date] = None) -> str:
    """返回近 N 个月的日期下限（ISO 文本）。

    与 deal_screening._cutoff_str 保持同一口径：每月按 30 天折算，
    半年即 180 天，保证两条链路的"近半年"判定完全一致。
    """
    current = now or datetime.now().date()
    return (current - timedelta(days=30 * months)).isoformat()


def _valid_deal_date(value: object) -> Optional[str]:
    """标准化成交日期文本；缺失或无法识别时返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def deduplicate_deal_records(
    records: Iterable[Mapping],
) -> list[Mapping]:
    """跨平台保守去重原始成交记录（RPA 回传的 deal_records 字典）。

    去重键 = (日期, 面积, 总价, 单价)：同一天同面积同价格的成交几乎
    不可能是两套独立成交，多为链家/房天下对同一套的真实成交各自记录。
    只合并精确相等的记录（保守），保留首次出现者；数量与面积不做任何
    容差合并，宁可漏合不可错合。
    """
    seen: set[tuple] = set()
    unique: list[Mapping] = []
    for record in records:
        key = (
            _valid_deal_date(record.get("date")),
            _as_float(record.get("area")),
            _as_float(record.get("total_price")),
            _as_float(record.get("price")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _as_float(value: object) -> Optional[float]:
    """宽松转 float；无法转换时返回 None（去重键中 None 只与 None 相等）。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def prepare_deal_samples(
    records_by_platform: Iterable[tuple[str, list[Mapping]]],
    request_area: float,
    *,
    now: Optional[date] = None,
) -> tuple[float, ...]:
    """把各平台原始成交记录收敛为可比市场的成交单价样本。

    处理顺序固定为"先跨平台去重，再按口径筛选"——去重键需要日期与
    总价等原始字段，若先筛选成纯价格列表便无法识别跨平台重复。

    口径（与需求确认一致）：
    - 面积：请求面积 ±10%（相对容差，见 area_rules.relative_area_bounds）；
    - 日期：近半年（DEAL_MONTHS_WINDOW）；日期缺失或无法识别的记录
      不保留，与 deal_screening 的既有口径一致；
    - 单价必须为正数。

    Args:
        records_by_platform: (平台名, 该平台原始成交记录列表) 序列；
            平台名当前不参与去重与筛选，保留参数便于日志与后续按平台扩展。
        request_area: 请求面积（㎡），相对容差的基准。
        now: 注入当前日期，测试用；缺省取系统当天。
    """
    merged = deduplicate_deal_records(
        record
        for _, records in records_by_platform
        for record in records
    )
    area_min, area_max = relative_area_bounds(request_area)
    cutoff = _cutoff_str(DEAL_MONTHS_WINDOW, now=now)

    samples: list[float] = []
    for record in merged:
        area = _as_float(record.get("area"))
        price = _as_float(record.get("price"))
        if area is None or price is None or price <= 0:
            continue
        if not (area_min <= area <= area_max):
            continue
        deal_date = _valid_deal_date(record.get("date"))
        if deal_date is None or deal_date < cutoff:
            continue
        samples.append(price)
    return tuple(samples)


def _peak_price(quote_price_lists: Iterable[Iterable[float]]) -> Optional[float]:
    """返回报价频率最高价格峰的落点中位数（裸价，不打折）。

    复用老算法的找峰函数（平台等权 + ±10% 密集簇识别），保证"频率
    最高价"的定义与老算法的价格峰口径同源。多个峰频率并列时取更低
    价格峰（保守估计）；无可成簇的报价返回 None。
    """
    # 函数级导入：weighted_median 底部的注册表反向引用本模块，
    # 顶层互相导入会形成循环。
    from app.algorithm.weighted_median import find_weighted_price_candidates

    candidates = find_weighted_price_candidates(
        quote_price_lists,
        quote_discount=1.0,
    )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: (candidate.frequency, -candidate.quote_price),
    ).quote_price


def _mean(values: Iterable[float]) -> Optional[float]:
    """算术平均；空序列返回 None。

    可比市场算法的成交与多小区均值均为简单平均（需求确认：先接受
    离群值风险，样本已经面积/日期/去重三道收敛）。
    """
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


def _relative_gap(left: float, right: float) -> float:
    """两个价格的相对差距，分母取较小者（按需求"不管谁做分母"）。"""
    return abs(left - right) / min(left, right)


def _target_listing_peaks(
    quote_price_lists: list[list[float]],
) -> list[PriceCandidate]:
    """目标小区报价峰列表（quote_discount=1.0，不做任何折扣），供审计。"""
    # 函数级导入，原因同 _peak_price。
    from app.algorithm.weighted_median import find_weighted_price_candidates

    return find_weighted_price_candidates(
        quote_price_lists,
        quote_discount=1.0,
    )


class MarketComparisonAlgorithm:
    """可比市场算法：四个业务场景的取值决策，全部为纯计算。

    注意：本算法不消费 weighted_median_discount 与 luxury_data_sparse，
    0.9 挂牌折扣与豪宅面积折扣是老算法（WEIGHTED_MEDIAN）专属口径；
    新算法的"不打折 / ×110%"已内建在各分支中。
    """

    def evaluate(self, inputs: AlgorithmInput) -> AlgorithmEvaluation:
        deal_samples = [
            price for price in inputs.deal_samples if price is not None and price > 0
        ]
        deal_mean = _mean(deal_samples)
        # P：目标小区报价频率最高价（可能为 None，如无挂牌或全部为孤立点）。
        listing_peak = _peak_price(inputs.quote_price_lists)
        peaks = _target_listing_peaks(inputs.quote_price_lists)

        if len(deal_samples) >= DEAL_SAMPLE_MIN_COUNT:
            # case1：样本充足，成交均价即最终价，报价仅作审计候选保留。
            return self._evaluation(
                final_price=deal_mean,
                branch=BRANCH_DEAL_AVG,
                quote_avg=listing_peak,
                deal_avg=deal_mean,
                candidates=peaks,
            )

        if deal_samples:
            # case2：样本不足（1~2 条）。有报价峰且与成交均值差距在容忍
            # 内 → 信报价；否则信成交并上浮折算（含报价峰缺失的兜底）。
            trust_listing = (
                listing_peak is not None
                and deal_mean is not None
                and _relative_gap(listing_peak, deal_mean) <= PRICE_GAP_TOLERANCE
            )
            if trust_listing:
                return self._evaluation(
                    final_price=listing_peak,
                    branch=BRANCH_LISTING_CONSISTENT,
                    quote_avg=listing_peak,
                    deal_avg=deal_mean,
                    candidates=peaks,
                )
            return self._evaluation(
                final_price=deal_mean * DEAL_ANCHOR_MARKUP,
                branch=BRANCH_DEAL_ANCHORED,
                quote_avg=listing_peak,
                deal_avg=deal_mean,
                candidates=peaks,
            )

        # 目标小区无成交样本：进入 case3 / case4，依赖周边小区数据。
        return self._evaluate_without_target_deals(
            inputs=inputs,
            listing_peak=listing_peak,
            candidates=peaks,
        )

    def _evaluate_without_target_deals(
        self,
        *,
        inputs: AlgorithmInput,
        listing_peak: Optional[float],
        candidates: list[PriceCandidate],
    ) -> AlgorithmEvaluation:
        """case3（周边有成交）与 case4（全无成交）的决策。

        边界约定：
        - 周边小区成交均值为空的样本不参与 D_near（需求："没有成交则
          不参与计算"）；全部周边都无成交 → 落入 case4；
        - 周边不足 3 个按实际数量计算（find_nearby 只返回实际命中数）；
        - 目标无报价峰（P=None）时的兜底：case3 取 D_near×110%，
          case4 只平均有峰的小区；
        - case4 连目标报价峰都没有 → FAILED，交由编排层按无数据归类。
        """
        nearby_deal_means = [
            mean
            for mean in (
                _mean(community.deal_prices)
                for community in inputs.nearby_communities
            )
            if mean is not None
        ]
        nearby_deal_avg = _mean(nearby_deal_means)

        if nearby_deal_avg is not None:
            # case3：周边成交均值高于报价（或报价缺失）→ 信周边成交并上浮；
            # 否则（D_near ≤ P，含相等）→ 取目标报价频率最高价。
            if listing_peak is None or nearby_deal_avg > listing_peak:
                return self._evaluation(
                    final_price=nearby_deal_avg * DEAL_ANCHOR_MARKUP,
                    branch=BRANCH_NEARBY_DEAL_ANCHORED,
                    quote_avg=listing_peak,
                    deal_avg=nearby_deal_avg,
                    candidates=candidates,
                )
            return self._evaluation(
                final_price=listing_peak,
                branch=BRANCH_NEARBY_DEAL_LOWER,
                quote_avg=listing_peak,
                deal_avg=nearby_deal_avg,
                candidates=candidates,
            )

        # case4：目标与周边均无成交，取各小区报价频率最高价的平均值。
        peak_prices = [
            peak
            for peak in [listing_peak]
            + [
                _peak_price(community.quote_price_lists)
                for community in inputs.nearby_communities
            ]
            if peak is not None
        ]
        average_peak = _mean(peak_prices)
        if average_peak is None:
            # 全链路无任何可用报价：按失败交编排层归类（NO_DATA 等）。
            return AlgorithmEvaluation(
                quote_avg=None,
                deal_avg=None,
                decision=Decision(final_price=None, branch="FAILED"),
                candidates=candidates,
            )
        return self._evaluation(
            final_price=average_peak,
            branch=BRANCH_LISTING_AVERAGE,
            quote_avg=listing_peak,
            deal_avg=None,
            candidates=candidates,
        )

    @staticmethod
    def _evaluation(
        *,
        final_price: Optional[float],
        branch: str,
        quote_avg: Optional[float],
        deal_avg: Optional[float],
        candidates: list[PriceCandidate],
    ) -> AlgorithmEvaluation:
        """统一组装评估结果：quote_avg 恒为目标报价峰（审计用），不打折。"""
        return AlgorithmEvaluation(
            quote_avg=quote_avg,
            deal_avg=deal_avg,
            decision=Decision(final_price=final_price, branch=branch),
            candidates=candidates,
        )
