"""The pull takes each ticket's comments: from the search result, or from the comment endpoint when it sent some."""

import copy

import httpx
import pytest
from fastapi import HTTPException
from knowledge_admin.jira.client import PULL_PAGE, JiraClient
from test_jira import ISSUES


def comment(number: int) -> dict:
    created = f'2026-10-15T10:{number:02d}:00.000+0000'
    return {
        'id': str(600 + number),
        'author': {'name': 'jira-user'},
        'body': f'Comment {number}',
        'created': created,
        'updated': created,
    }


def pull(issues: list[dict], all_comments: dict[str, list[dict]] | None = None, limit: int = 3):
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/comment'):
            rows = (all_comments or {})[request.url.path.split('/')[-2]]
            start, size = int(request.url.params['startAt']), 2
            return httpx.Response(
                200, json={'startAt': start, 'total': len(rows), 'comments': rows[start : start + size]}
            )
        start, size = int(request.url.params['startAt']), int(request.url.params['maxResults'])
        return httpx.Response(
            200, json={'startAt': start, 'total': len(issues), 'issues': copy.deepcopy(issues[start : start + size])}
        )

    with JiraClient('https://jira.internal/jira', 'private-jira-token', httpx.MockTransport(handler)) as jira:
        return jira.tickets(limit)[0], requests


def test_comments_in_the_search_result_are_kept_oldest_first_without_extra_requests():
    issues = copy.deepcopy(ISSUES[:2])
    issues[0]['fields']['comment'] = {'startAt': 0, 'maxResults': 2, 'total': 2, 'comments': [comment(2), comment(1)]}
    pulled, requests = pull(issues)
    assert [row['id'] for row in pulled[0]['fields']['comment']['comments']] == ['601', '602']
    assert pulled[0]['fields']['comment']['total'] == 2
    assert pulled[1]['fields']['comment']['comments'] == []  # no comment field: none
    assert len(requests) == 1
    assert requests[0].url.params['fields'].split(',')[-1] == 'comment'


def test_a_partial_comment_list_is_completed_from_the_comment_endpoint():
    issues = copy.deepcopy(ISSUES[:1])
    rows = [comment(number) for number in range(1, 6)]
    issues[0]['fields']['comment'] = {'startAt': 0, 'maxResults': 1, 'total': 5, 'comments': rows[:1]}
    pulled, requests = pull(issues, {issues[0]['id']: rows})
    assert [row['body'] for row in pulled[0]['fields']['comment']['comments']] == [f'Comment {n}' for n in range(1, 6)]
    assert [request.url.path.endswith('/comment') for request in requests] == [False, True, True, True]


@pytest.mark.parametrize(
    'embedded',
    [
        {'startAt': 0, 'maxResults': 1, 'total': 0, 'comments': [comment(1)]},  # more rows than total
        {'startAt': 0, 'maxResults': 2, 'total': 2, 'comments': [comment(1), comment(1)]},  # repeated id
        {'startAt': 0, 'total': 1, 'comments': [{**comment(1), 'created': '2026-10-15T10:00:00'}]},  # no timezone
    ],
)
def test_invalid_comments_fail_the_pull(embedded):
    issues = copy.deepcopy(ISSUES[:1])
    issues[0]['fields']['comment'] = embedded
    with pytest.raises(HTTPException) as error:
        pull(issues)
    assert error.value.status_code == 502


def test_search_pages_hold_twenty_tickets():
    issues = [{**copy.deepcopy(ISSUES[0]), 'id': str(20000 + n), 'key': f'DEMO-{200 + n}'} for n in range(45)]
    pulled, requests = pull(issues, limit=45)
    assert len(pulled) == 45 and PULL_PAGE == 20
    assert [int(request.url.params['maxResults']) for request in requests] == [20, 20, 5]
