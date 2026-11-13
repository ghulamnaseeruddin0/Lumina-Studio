"""Base class for all Lumina agents."""


class Agent:
    id = ""
    number = 0
    title = ""
    group = ""
    kind = "code"          # "ai", "code" or "ai+code"
    how = ""               # plain-language explanation shown live in the interface
    critical = True        # if False, a failure becomes a warning and the job continues
    state_key = None       # where the result is stored in job.state (defaults to the agent id)

    def run(self, job, phase=None):  # pragma: no cover - interface
        raise NotImplementedError

    def meta(self):
        return {"id": self.id, "number": self.number, "title": self.title, "group": self.group,
                "kind": self.kind, "how": self.how}
