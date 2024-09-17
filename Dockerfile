FROM --platform=linux/amd64 pypy
RUN apt update && apt install -yq libzstd-dev cargo build-essential libgeos-dev
WORKDIR /srv
COPY ./ /srv/cadevil/
WORKDIR /srv/cadevil
RUN pip install -r requirements.txt && pypy manage.py collectstatic
ENTRYPOINT make debug