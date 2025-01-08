import json

from channels.generic.websocket import WebsocketConsumer, AsyncWebsocketConsumer


class SocketConsumer(WebsocketConsumer):
    async def connect(self):
        self.accept()

    async def disconnect(self, close_code):
        pass

    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        message = text_data_json["message"]

        self.send(text_data=json.dumps({"message": message}))


class ProgressConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.task_id = self.scope["url_route"]["kwargs"]["task_id"]

        await self.channel_layer.group_add(self.task_id, self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.task_id, self.channel_name)

    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        task_type = text_data_json["type"]

        if task_type == "check_task_completion":
            await self.channel_layer.group_send(
                self.task_id,
                {
                    "type": "update_task_progress",
                    "data": Progress(AsyncResult(self.task_id)).get_info(),
                },
            )

    async def update_task_progress(self, event):
        data = event["data"]

        await self.send(text_data=json.dumps(data))


class LogConsumer(AsyncWebsocketConsumer):
    group_name = "log_stream"

    async def connect(self):
        # Accept the WebSocket connection
        # print("Connecting...")
        await self.accept()
        # Join the log_stream group
        await self.channel_layer.group_add("log_stream", self.channel_name)

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

    async def disconnect(self, close_code):
        # print("Disconnecting...")

        # Leave the group on disconnect
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def new_log(self, event):
        """
        This method is called when the group_send sends a message with "type": "new_log".
        The "message" key in event contains the log text.
        """
        # print("new_log called:", event["message"])  # debug
        message = event["message"]
        # Send the message to the WebSocket
        await self.send(text_data=message)

    async def receive(self, text_data=None, bytes_data=None):
        """
        If you want to handle messages from the client, implement your logic here.
        Otherwise, a no-op method is sufficient if the client doesn't send anything.
        """
        pass
        # print("Receive")
        # text_data_json = json.loads(text_data)
        # message = text_data_json["message"]
        #
        # await self.send(text_data=json.dumps({"message": message}))
