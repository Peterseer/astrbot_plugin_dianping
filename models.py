from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Dish:
    name: str
    price: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class Restaurant:
    shop_id: str
    name: str
    score: float | None = None
    review_count: str = ""
    avg_price: str = ""
    category: str = ""
    area: str = ""
    address: str = ""
    dishes: list[Dish] = field(default_factory=list)
    detail_url: str = ""
    phone: str = ""
    sub_scores: dict[str, str] = field(default_factory=dict)
    source_rank: int = 0
    rank_score: float = 0.0

    def to_dict(
        self, *, include_phone: bool = False, include_shop_id: bool = False
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "score": self.score,
            "review_count": self.review_count,
            "avg_price": self.avg_price,
            "category": self.category,
            "area": self.area,
            "address": self.address,
            "signature_dishes": [dish.to_dict() for dish in self.dishes],
            "detail_url": self.detail_url,
        }
        if self.sub_scores:
            data["sub_scores"] = self.sub_scores
        if include_shop_id:
            data["shop_id"] = self.shop_id
        if include_phone:
            data["phone"] = self.phone
        return data
