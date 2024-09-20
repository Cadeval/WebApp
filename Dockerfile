#FROM python
FROM python:3.12.3-slim
RUN apt update && apt dist-upgrade -yq && apt install -yq cargo build-essential git libgeos-dev && apt autoremove -yq && apt autoclean -yq
ADD requirements.txt
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt && python manage.py collectstatic
#WORKDIR /srv
COPY src /srv/cadevil/
WORKDIR /srv/cadevil
ENTRYPOINT make debug