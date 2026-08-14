import msgspec
from django.contrib.auth import get_user_model
from django_bolt import BoltAPI

User = get_user_model()

api = BoltAPI()


class UserSchema(msgspec.Struct):
    id: int
    username: str


@api.get("/users/{user_id}")
async def get_user(user_id: int):  # 🎉 Response is type validated
    user = await User.objects.aget(
        id=user_id
    )  # 🤯 Yes and Django orm works without any setup
    return {
        "id": user.id,
        "username": user.get_username(),
    }  # or you could just return the queryset
