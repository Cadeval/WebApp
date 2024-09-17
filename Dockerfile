FROM python:3.13.0rc2-slim
RUN apt update && apt install -yq libzstd-dev cargo build-essential libgeos-dev && apt autoremove -yq && apt autoclean -yq && rm -rf $HOME/.cache
WORKDIR /srv
COPY ./ /srv/cadevil/
WORKDIR /srv/cadevil
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt && python manage.py collectstatic
ENTRYPOINT make debug