from pydantic import BaseModel


class EndpointCounts(BaseModel):
    total: int
    active: int
    disabled: int


class StatsOut(BaseModel):
    window_hours: int
    events: int
    deliveries: dict[str, int]  # pending / succeeded / dead, created within the window
    dead_total: (
        int  # all-time: the dead-letter queue size, which needs attention whenever it is > 0
    )
    endpoints: EndpointCounts
