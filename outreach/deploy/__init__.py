"""The deploy tool (#17): install the new platform's launchd jobs, retire the old ones, and roll back.

`python -m outreach.deploy plan` and `status` read only and run anywhere. `install`, `retire-old` and `rollback`
change launchd and run only from the live checkout. Standard library only.
"""
from outreach.deploy.jobs import NEW_JOBS, OLD_JOBS, DeployError, Job, render

__all__ = ["NEW_JOBS", "OLD_JOBS", "DeployError", "Job", "render"]
