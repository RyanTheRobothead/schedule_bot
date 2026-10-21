from datetime import date

from schedule_bot.models import Status, UserResponse
from schedule_bot.scoring import rank, summarize

P, A, T, U = Status.PREFERRED, Status.AVAILABLE, Status.TENTATIVE, Status.UNAVAILABLE
D1, D2, D3 = date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)


def test_default_with_exceptions():
    r = UserResponse(A, {D2: U, D3: P})
    assert [r.status_on(d) for d in (D1, D2, D3)] == [A, U, P]


def test_one_unavailable_outranks_any_amount_of_enthusiasm():
    responses = {i: UserResponse(P if i < 4 else U) for i in range(5)}  # D1: 4 preferred, 1 unavailable
    for i in range(5):
        responses[i].overrides[D2] = A  # D2: everyone merely available
    best = rank(summarize([D1, D2], responses))
    assert [s.day for s in best] == [D2, D1]


def test_tie_breaks_on_tentative_then_preferred_then_date():
    responses = {
        1: UserResponse(A, {D1: T, D3: P}),
        2: UserResponse(A),
    }
    assert [s.day for s in rank(summarize([D1, D2, D3], responses))] == [D3, D2, D1]


def test_non_responders_ignored_and_partial_answers_tracked():
    responses = {1: UserResponse(), 2: UserResponse(None, {D1: P})}
    s1, s2 = summarize([D1, D2], responses)
    assert s1.by_status[P] == [2] and s1.total == 1
    assert s2.no_answer == [2] and s2.can_make_it == 0
    assert [s.day for s in rank([s1, s2])] == [D1]


def test_days_nobody_can_make_are_dropped():
    assert rank(summarize([D1], {1: UserResponse(U)})) == []
