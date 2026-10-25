from .arbeitnow import ArbeitnowCollector
from .ashby import AshbyCollector
from .greenhouse import GreenhouseCollector
from .himalayas import HimalayasCollector
from .lever import LeverCollector
from .remotive import RemotiveCollector

COLLECTORS = {
    "himalayas": HimalayasCollector,
    "greenhouse": GreenhouseCollector,
    "ashby": AshbyCollector,
    "lever": LeverCollector,
    "remotive": RemotiveCollector,
    "arbeitnow": ArbeitnowCollector,
}
