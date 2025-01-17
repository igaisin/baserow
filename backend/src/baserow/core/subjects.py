from typing import List

from django.contrib.auth.models import AbstractUser, AnonymousUser

from baserow.core.models import Group, GroupUser, User
from baserow.core.registries import SubjectType
from baserow.core.types import Subject


class UserSubjectType(SubjectType):

    type = "auth.User"
    model_class = User

    def are_in_group(self, subjects: List[Subject], group: Group) -> List[bool]:
        """
        Check whether the given subjects ar member of the given group.
        """

        user_ids_in_group = GroupUser.objects.filter(
            user__in=subjects,
            group=group,
            user__profile__to_be_deleted=False,
            user__is_active=True,
        ).values_list("user_id", flat=True)

        return [s.id in user_ids_in_group for s in subjects]

    def get_serializer(self, model_instance, **kwargs):
        from baserow.api.user.serializers import SubjectUserSerializer

        return SubjectUserSerializer(model_instance, **kwargs)

    def get_users_included_in_subject(
        self, subject: AbstractUser
    ) -> List[AbstractUser]:
        return [subject]


class AnonymousUserSubjectType(SubjectType):

    type = "anonymous"
    model_class = AnonymousUser

    def are_in_group(self, subjects: List[Subject], group: Group) -> List[bool]:
        """
        Anonymous users are never member of any group.
        """

        return [False for _ in subjects]

    def get_serializer(self, model_instance, **kwargs):
        return None

    def get_users_included_in_subject(
        self, subject: AnonymousUser
    ) -> List[AbstractUser]:
        return []
