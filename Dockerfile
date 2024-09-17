FROM python:3.13.0rc2-slim
RUN apt update && apt install -yq libzstd-dev cargo build-essential libgeos-dev && apt autoremove -yq && apt autoclean -yq && rm -rf $HOME/.cache
WORKDIR /srv
COPY ./ /srv/cadevil/
WORKDIR /srv/cadevil
RUN pypy -m pip install --upgrade pip && pypy -m pip install -r requirements.txt && pypy manage.py collectstatic
ENTRYPOINT make debug