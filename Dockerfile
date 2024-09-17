#FROM python
FROM python:3.12.3-slim
RUN apt update && apt dist-upgrade -yq && apt install -yq cargo build-essential git libgeos-dev && apt autoremove -yq && apt autoclean -yq
WORKDIR /srv
COPY ./ /srv/cadevil/
WORKDIR /srv/cadevil
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt && python manage.py collectstatic
ENTRYPOINT make debug