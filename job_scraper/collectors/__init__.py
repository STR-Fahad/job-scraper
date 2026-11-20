from .arbeitnow import ArbeitnowCollector
from .ashby import AshbyCollector
from .greenhouse import GreenhouseCollector
from .himalayas import HimalayasCollector
from .jobicy import JobicyCollector
from .lever import LeverCollector
from .remoteok import RemoteOkCollector
from .remotive import RemotiveCollector
from .weworkremotely import WeWorkRemotelyCollector
from .workingnomads import WorkingNomadsCollector

COLLECTORS = {
    "himalayas": HimalayasCollector,
    "greenhouse": GreenhouseCollector,
    "ashby": AshbyCollector,
    "lever": LeverCollector,
    "remotive": RemotiveCollector,
    "arbeitnow": ArbeitnowCollector,
    "jobicy": JobicyCollector,
    "remoteok": RemoteOkCollector,
    "workingnomads": WorkingNomadsCollector,
    "weworkremotely": WeWorkRemotelyCollector,
}
