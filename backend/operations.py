"""Human-entered execution evidence, kept separate from model recommendations."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    planned_date: date | None = None
    observation_start: date | None = None
    observation_end: date | None = None
    actual_action: str = Field(default="", max_length=1500)
    metric_name: str = Field(default="", max_length=150)
    metric_unit: str = Field(default="", max_length=30)
    baseline_value: float | None = Field(default=None, allow_inf_nan=False)
    outcome_value: float | None = Field(default=None, allow_inf_nan=False)
    data_reference: str = Field(default="", max_length=500)
    confounders: str = Field(default="", max_length=1000)
    evaluation_status: Literal["pending", "observed", "inconclusive"] = "pending"
    evaluation_note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def complete_observation(self):
        if self.observation_start and self.observation_end and self.observation_end < self.observation_start:
            raise ValueError("观察结束日期不能早于开始日期")
        if self.evaluation_status == "observed":
            if not all((self.actual_action, self.metric_name, self.metric_unit, self.data_reference, self.observation_start, self.observation_end)) or self.baseline_value is None or self.outcome_value is None:
                raise ValueError("观察结果须填写实际动作、观察窗口、指标单位、基线、结果及数据来源")
        if self.evaluation_status == "inconclusive" and not self.evaluation_note:
            raise ValueError("无法评估时请记录原因")
        return self
