from django.db import OperationalError, connection
from django.utils import timezone

import pytest
from freezegun import freeze_time

from baserow.contrib.database.fields.models import Field
from baserow.contrib.database.rows.handler import RowHandler
from baserow.contrib.database.table.models import Table
from baserow.core.exceptions import ApplicationDoesNotExist, GroupDoesNotExist
from baserow.core.models import Application, Group, TrashEntry
from baserow.core.trash.exceptions import (
    CannotDeleteAlreadyDeletedItem,
    CannotRestoreChildBeforeParent,
    PermanentDeletionMaxLocksExceededException,
)
from baserow.core.trash.handler import TrashHandler, _get_trash_entry


@pytest.mark.django_db
def test_trashing_an_item_creates_a_trash_entry_in_the_db_and_marks_it_as_trashed(
    data_fixture,
):
    user = data_fixture.create_user()
    group_to_delete = data_fixture.create_group(user=user)
    assert not group_to_delete.trashed
    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, group_to_delete, None, group_to_delete)
    assert group_to_delete.trashed
    trash_entry = TrashEntry.objects.get(
        trash_item_id=group_to_delete.id, trash_item_type="group"
    )
    assert trash_entry.trashed_at.isoformat() == "2020-01-01T12:00:00+00:00"
    assert Group.objects.count() == 0
    assert Group.trash.count() == 1


@pytest.mark.django_db
def test_restoring_a_trashed_item_unmarks_it_as_trashed_and_deletes_the_entry(
    data_fixture,
):
    user = data_fixture.create_user()
    group_to_delete = data_fixture.create_group(user=user)
    TrashHandler.trash(user, group_to_delete, None, group_to_delete)
    assert group_to_delete.trashed
    assert TrashEntry.objects.count() == 1

    TrashHandler.restore_item(user, "group", group_to_delete.id)

    group_to_delete.refresh_from_db()
    assert not group_to_delete.trashed
    assert TrashEntry.objects.count() == 0
    assert Group.trash.count() == 0
    assert Group.objects.count() == 1


@pytest.mark.django_db
def test_a_trash_entry_older_than_setting_gets_marked_for_permanent_deletion(
    data_fixture, settings
):
    user = data_fixture.create_user()
    group_to_delete = data_fixture.create_group(user=user)

    trashed_at = timezone.now()
    half_time = timezone.timedelta(
        hours=settings.HOURS_UNTIL_TRASH_PERMANENTLY_DELETED / 2
    )
    plus_one_hour_over = timezone.timedelta(
        hours=settings.HOURS_UNTIL_TRASH_PERMANENTLY_DELETED + 1
    )
    with freeze_time(trashed_at):
        TrashHandler.trash(user, group_to_delete, None, group_to_delete)

    entry = _get_trash_entry("group", None, group_to_delete.id)
    assert not entry.should_be_permanently_deleted

    datetime_when_trash_item_should_still_be_kept = trashed_at + half_time
    with freeze_time(datetime_when_trash_item_should_still_be_kept):
        TrashHandler.mark_old_trash_for_permanent_deletion()

    entry.refresh_from_db()
    assert not entry.should_be_permanently_deleted

    datetime_when_trash_item_old_enough_to_be_deleted = trashed_at + plus_one_hour_over
    with freeze_time(datetime_when_trash_item_old_enough_to_be_deleted):
        TrashHandler.mark_old_trash_for_permanent_deletion()

    entry.refresh_from_db()
    assert entry.should_be_permanently_deleted


@pytest.mark.django_db
def test_a_trash_entry_marked_for_permanent_deletion_gets_deleted_by_task(
    data_fixture, settings
):
    user = data_fixture.create_user()
    group_to_delete = data_fixture.create_group(user=user)

    trashed_at = timezone.now()
    plus_one_hour_over = timezone.timedelta(
        hours=settings.HOURS_UNTIL_TRASH_PERMANENTLY_DELETED + 1
    )
    with freeze_time(trashed_at):
        TrashHandler.trash(user, group_to_delete, None, group_to_delete)

    TrashHandler.permanently_delete_marked_trash()
    assert Group.trash.count() == 1

    datetime_when_trash_item_old_enough_to_be_deleted = trashed_at + plus_one_hour_over
    with freeze_time(datetime_when_trash_item_old_enough_to_be_deleted):
        TrashHandler.mark_old_trash_for_permanent_deletion()

    TrashHandler.permanently_delete_marked_trash()
    assert Group.objects.count() == 0


@pytest.mark.django_db
def test_a_group_marked_for_perm_deletion_raises_a_404_when_asked_for_trash_contents(
    data_fixture,
):
    user = data_fixture.create_user()
    group_to_delete = data_fixture.create_group(user=user)
    assert not group_to_delete.trashed
    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, group_to_delete, None, group_to_delete)
    trash_entry = TrashEntry.objects.get(
        trash_item_id=group_to_delete.id, trash_item_type="group"
    )
    trash_entry.should_be_permanently_deleted = True
    trash_entry.save()

    with pytest.raises(GroupDoesNotExist):
        TrashHandler.get_trash_contents(user, group_to_delete.id, None)
    with pytest.raises(GroupDoesNotExist):
        TrashHandler.get_trash_contents_for_emptying(user, group_to_delete.id, None)


@pytest.mark.django_db
def test_a_group_marked_for_perm_deletion_no_longer_shows_up_in_trash_structure(
    data_fixture,
):
    user = data_fixture.create_user()
    group_to_delete = data_fixture.create_group(user=user)
    assert not group_to_delete.trashed
    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, group_to_delete, None, group_to_delete)
    trash_entry = TrashEntry.objects.get(
        trash_item_id=group_to_delete.id, trash_item_type="group"
    )
    trash_entry.should_be_permanently_deleted = True
    trash_entry.save()

    assert len(TrashHandler.get_trash_structure(user)["groups"]) == 0


@pytest.mark.django_db
def test_an_app_marked_for_perm_deletion_raises_a_404_when_asked_for_trash_contents(
    data_fixture,
):
    user = data_fixture.create_user()
    group = data_fixture.create_group(user=user)
    trashed_database = data_fixture.create_database_application(user=user, group=group)
    assert not trashed_database.trashed
    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, group, trashed_database, trashed_database)
    trash_entry = TrashEntry.objects.get(
        trash_item_id=trashed_database.id, trash_item_type="application"
    )
    trash_entry.should_be_permanently_deleted = True
    trash_entry.save()

    with pytest.raises(ApplicationDoesNotExist):
        TrashHandler.get_trash_contents(user, group.id, trashed_database.id)
    with pytest.raises(ApplicationDoesNotExist):
        TrashHandler.get_trash_contents_for_emptying(
            user, group.id, trashed_database.id
        )


@pytest.mark.django_db
def test_a_trashed_app_shows_up_in_trash_structure(
    data_fixture,
):
    user = data_fixture.create_user()
    group = data_fixture.create_group(user=user)
    trashed_database = data_fixture.create_database_application(user=user, group=group)
    assert not trashed_database.trashed
    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, group, trashed_database, trashed_database)

    structure = TrashHandler.get_trash_structure(user)
    applications = structure["groups"][0]["applications"]
    assert len(applications) == 1
    assert applications[0].trashed


@pytest.mark.django_db
def test_an_app_marked_for_perm_deletion_no_longer_shows_up_in_trash_structure(
    data_fixture,
):
    user = data_fixture.create_user()
    group = data_fixture.create_group(user=user)
    trashed_database = data_fixture.create_database_application(user=user, group=group)
    assert not trashed_database.trashed
    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, group, trashed_database, trashed_database)
    trash_entry = TrashEntry.objects.get(
        trash_item_id=trashed_database.id, trash_item_type="application"
    )
    trash_entry.should_be_permanently_deleted = True
    trash_entry.save()

    for group in TrashHandler.get_trash_structure(user)["groups"]:
        assert len(group["applications"]) == 0


@pytest.mark.django_db(transaction=True)
def test_perm_deleting_a_parent_with_a_trashed_child_also_cleans_up_the_child_entry(
    data_fixture,
):
    user = data_fixture.create_user()
    group = data_fixture.create_group(user=user)
    database = data_fixture.create_database_application(user=user, group=group)
    table = data_fixture.create_database_table(database=database)
    field = data_fixture.create_text_field(user=user, table=table)
    table_model = table.get_model()
    row = table_model.objects.create(**{f"field_{field.id}": "Test"})

    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, group, database, row, parent_id=table.id)
        TrashHandler.trash(user, group, database, field)
        TrashHandler.trash(user, group, database, table)
        TrashHandler.trash(user, group, database, database)
        TrashHandler.trash(user, group, None, group)

    TrashHandler.empty(user, group.id, None)

    assert TrashEntry.objects.count() == 5

    TrashHandler.permanently_delete_marked_trash()

    assert TrashEntry.objects.count() == 0
    assert Group.objects_and_trash.count() == 0
    assert Application.objects_and_trash.count() == 0
    assert Table.objects_and_trash.count() == 0
    assert Field.objects_and_trash.count() == 0
    assert f"database_table_{table.id}" not in connection.introspection.table_names()


@pytest.mark.django_db
def test_perm_deleting_a_table_with_a_trashed_row_also_cleans_up_the_row_entry(
    data_fixture,
):
    user = data_fixture.create_user()
    group = data_fixture.create_group(user=user)
    database = data_fixture.create_database_application(user=user, group=group)
    table = data_fixture.create_database_table(database=database)
    field = data_fixture.create_text_field(user=user, table=table)
    table_model = table.get_model()
    row = table_model.objects.create(**{f"field_{field.id}": "Test"})

    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, group, database, database)
        TrashHandler.trash(user, group, database, row, parent_id=table.id)
        TrashHandler.trash(user, group, database, table)

    TrashHandler.empty(user, group.id, database.id)

    assert TrashEntry.objects.count() == 3

    TrashHandler.permanently_delete_marked_trash()

    assert TrashEntry.objects.count() == 0
    assert Table.objects_and_trash.count() == 0
    assert Field.objects_and_trash.count() == 0
    assert Application.objects_and_trash.count() == 0
    assert f"database_table_{table.id}" not in connection.introspection.table_names()


@pytest.mark.django_db
def test_trash_contents_are_ordered_from_newest_to_oldest_entries(
    data_fixture,
):
    user = data_fixture.create_user()
    group = data_fixture.create_group(user=user)
    trashed_database = data_fixture.create_database_application(user=user, group=group)

    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, group, trashed_database, trashed_database)
    with freeze_time("2020-01-01 12:02"):
        TrashHandler.trash(user, group, None, group)

    contents = TrashHandler.get_trash_contents(user, group.id, None)

    assert contents[0].trash_item_type == "group"
    assert contents[0].trash_item_id == group.id
    assert contents[0].trashed_at.isoformat() == "2020-01-01T12:02:00+00:00"

    assert contents[1].trash_item_type == "application"
    assert contents[1].trash_item_id == trashed_database.id
    assert contents[1].trashed_at.isoformat() == "2020-01-01T12:00:00+00:00"


@pytest.mark.django_db
def test_perm_deleting_one_group_should_not_effect_another_trashed_group(
    data_fixture,
):
    user = data_fixture.create_user()
    trashed_group = data_fixture.create_group(user=user)
    other_trashed_group = data_fixture.create_group(user=user)
    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, trashed_group, None, trashed_group)
        TrashHandler.trash(user, other_trashed_group, None, other_trashed_group)

    # Only mark one for deletion
    parent_trash_entry = TrashEntry.objects.get(
        trash_item_id=trashed_group.id, trash_item_type="group"
    )
    parent_trash_entry.should_be_permanently_deleted = True
    parent_trash_entry.save()

    assert TrashEntry.objects.count() == 2
    assert TrashEntry.objects.filter(should_be_permanently_deleted=True).count() == 1
    assert Group.objects_and_trash.count() == 2

    TrashHandler.permanently_delete_marked_trash()

    assert TrashEntry.objects.count() == 1
    assert Group.objects_and_trash.count() == 1


@pytest.mark.django_db
def test_deleting_a_user_who_trashed_items_should_still_leave_those_items_trashed(
    data_fixture,
):
    user = data_fixture.create_user()
    trashed_group = data_fixture.create_group(user=user)
    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(user, trashed_group, None, trashed_group)

    assert TrashEntry.objects.count() == 1
    assert Group.objects_and_trash.count() == 1

    user.delete()

    assert TrashEntry.objects.count() == 1
    assert Group.objects_and_trash.count() == 1


@pytest.mark.django_db
def test_trashing_two_rows_in_different_tables_works_as_expected(
    data_fixture,
):
    user = data_fixture.create_user()
    table_1 = data_fixture.create_database_table(name="Car", user=user)
    table_2 = data_fixture.create_database_table(name="Other Cars", user=user)
    group = data_fixture.create_group(user=user)
    name_field = data_fixture.create_text_field(
        table=table_1, name="Name", text_default="Test"
    )

    handler = RowHandler()

    row_in_table_1 = handler.create_row(
        user=user,
        table=table_1,
        values={
            name_field.id: "Tesla",
        },
    )
    row_in_table_2 = handler.create_row(
        user=user,
        table=table_2,
        values={
            name_field.id: "Ford",
        },
    )
    with freeze_time("2020-01-01 12:00"):
        TrashHandler.trash(
            user, group, table_1.database, row_in_table_1, parent_id=table_1.id
        )
        TrashHandler.trash(
            user, group, table_2.database, row_in_table_2, parent_id=table_2.id
        )

    table_1_model = table_1.get_model()
    table_2_model = table_2.get_model()

    assert table_1_model.trash.count() == 1
    assert table_1_model.objects.count() == 0

    assert table_2_model.trash.count() == 1
    assert table_2_model.objects.count() == 0

    TrashHandler.restore_item(
        user, "row", row_in_table_1.id, parent_trash_item_id=table_1.id
    )

    assert table_1_model.trash.count() == 0
    assert table_1_model.objects.count() == 1

    assert table_2_model.trash.count() == 1
    assert table_2_model.objects.count() == 0


@pytest.mark.django_db
def test_cannot_restore_a_child_before_the_parent(
    data_fixture,
):
    user = data_fixture.create_user()
    table_1 = data_fixture.create_database_table(name="Car", user=user)
    group = table_1.database.group
    name_field = data_fixture.create_text_field(
        table=table_1, name="Name", text_default="Test"
    )

    handler = RowHandler()

    row_in_table_1 = handler.create_row(
        user=user,
        table=table_1,
        values={
            name_field.id: "Tesla",
        },
    )
    TrashHandler.trash(
        user, group, table_1.database, row_in_table_1, parent_id=table_1.id
    )
    TrashHandler.trash(user, group, table_1.database, table_1)

    with pytest.raises(CannotRestoreChildBeforeParent):
        TrashHandler.restore_item(
            user, "row", row_in_table_1.id, parent_trash_item_id=table_1.id
        )

    TrashHandler.trash(user, group, table_1.database, table_1.database)
    TrashHandler.trash(user, group, None, group)

    with pytest.raises(CannotRestoreChildBeforeParent):
        TrashHandler.restore_item(user, "application", table_1.database.id)

    TrashHandler.restore_item(user, "group", group.id)

    with pytest.raises(CannotRestoreChildBeforeParent):
        TrashHandler.restore_item(user, "table", table_1.id)


@pytest.mark.django_db
def test_cant_trash_same_item_twice(
    data_fixture,
):
    user = data_fixture.create_user()
    group_to_delete = data_fixture.create_group(user=user)
    TrashHandler.trash(user, group_to_delete, None, group_to_delete)
    with pytest.raises(CannotDeleteAlreadyDeletedItem):
        TrashHandler.trash(user, group_to_delete, None, group_to_delete)
    assert (
        TrashEntry.objects.filter(
            trash_item_id=group_to_delete.id, trash_item_type="group"
        ).count()
        == 1
    )
    assert Group.objects.count() == 0
    assert Group.trash.count() == 1


@pytest.mark.django_db
def test_cant_trash_same_row_twice(
    data_fixture,
):
    user = data_fixture.create_user()
    table, fields, rows = data_fixture.build_table(
        columns=[("text", "text")], rows=["first row", "second_row"], user=user
    )
    TrashHandler.trash(
        user, table.database.group, table.database, rows[0], parent_id=table.id
    )
    with pytest.raises(CannotDeleteAlreadyDeletedItem):
        TrashHandler.trash(
            user, table.database.group, table.database, rows[0], parent_id=table.id
        )
    assert (
        TrashEntry.objects.filter(
            trash_item_id=rows[0].id,
            trash_item_type="row",
            parent_trash_item_id=table.id,
        ).count()
        == 1
    )
    model = table.get_model()
    assert model.objects.count() == 1
    assert model.trash.count() == 1


@pytest.mark.django_db
def test_permanently_delete_item_raises_operationalerror(
    data_fixture,
    bypass_check_permissions,
    trash_item_type_perm_delete_item_raising_operationalerror,
):
    trash_item_lookup_cache = {}
    user = data_fixture.create_user()
    group_to_delete = data_fixture.create_group(user=user)
    trash_entry = TrashHandler.trash(user, group_to_delete, None, group_to_delete)

    with trash_item_type_perm_delete_item_raising_operationalerror(
        raise_transaction_exception=True
    ):
        with pytest.raises(PermanentDeletionMaxLocksExceededException):
            TrashHandler.try_perm_delete_trash_entry(
                trash_entry, trash_item_lookup_cache
            )

    with trash_item_type_perm_delete_item_raising_operationalerror(
        raise_transaction_exception=False
    ):
        with pytest.raises(OperationalError):
            TrashHandler.try_perm_delete_trash_entry(
                trash_entry, trash_item_lookup_cache
            )
