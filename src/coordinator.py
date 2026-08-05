"""Case orchestration and output gating. Owned by Member 1."""


def process_case(ticket: dict, repository: object) -> dict:
    """Run domain agents, policy, then verifier for one released ticket.

    TODO(M1): return an output document only after verifier approval.
    """

    raise NotImplementedError

