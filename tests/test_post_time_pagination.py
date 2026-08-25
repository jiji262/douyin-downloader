from core.user_modes.post_time_boundary import PostTimeBoundary


def _item(aweme_id: str, create_time=None, *, is_top=False):
    item = {"aweme_id": aweme_id, "is_top": is_top}
    if create_time is not None:
        item["create_time"] = create_time
    return item


def test_boundary_requires_one_all_old_confirmation_page():
    boundary = PostTimeBoundary(200)
    assert boundary.observe_page([_item("new", 300)]).should_stop is False
    assert boundary.observe_page([_item("in", 220), _item("old", 190)]).should_stop is False
    assert boundary.observe_page([_item("older", 180)]).should_stop is True


def test_old_pinned_item_does_not_start_boundary():
    boundary = PostTimeBoundary(200)
    decision = boundary.observe_page(
        [_item("pinned", 100, is_top=True), _item("new", 300)],
        is_pinned=lambda item: bool(item.get("is_top")),
    )
    assert decision.should_stop is False
    assert boundary.observe_page([_item("in", 220), _item("old", 190)]).should_stop is False
    assert boundary.observe_page([_item("older", 180)]).should_stop is True


def test_missing_time_disables_boundary_once():
    boundary = PostTimeBoundary(200)
    decision = boundary.observe_page([_item("missing")])
    assert decision.degraded_reason == "missing_or_invalid_create_time"
    assert boundary.observe_page([_item("old", 100)]).degraded_reason is None


def test_in_page_or_cross_page_time_increase_disables_boundary():
    in_page = PostTimeBoundary(200)
    assert (
        in_page.observe_page([_item("a", 300), _item("b", 310)]).degraded_reason
        == "time_order_increased"
    )

    cross_page = PostTimeBoundary(200)
    cross_page.observe_page([_item("a", 300), _item("b", 250)])
    assert cross_page.observe_page([_item("c", 260)]).degraded_reason == (
        "time_order_increased"
    )


def test_confirmation_page_without_regular_items_disables_boundary():
    boundary = PostTimeBoundary(200)
    boundary.observe_page([_item("in", 220), _item("old", 190)])
    decision = boundary.observe_page(
        [_item("pinned", 100, is_top=True)],
        is_pinned=lambda _: True,
    )
    assert decision.degraded_reason == "confirmation_page_without_regular_items"
