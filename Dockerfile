FROM pypy
RUN apt update && apt dist-upgrade -yq && apt install -yq cargo build-essential git libgeos-dev && apt autoremove -yq && apt autoclean -yq
WORKDIR /srv
COPY ./ /srv/cadevil/
WORKDIR /srv/cadevil
RUN pypy -m pip install --upgrade pip && pypy -m pip install -r requirements.txt && pypy manage.py collectstatic
ENTRYPOINT make debug