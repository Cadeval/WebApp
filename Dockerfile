FROM --platform=linux/amd64 python
WORKDIR /srv
ADD * /srv/
RUN pip install -r requirements.txt