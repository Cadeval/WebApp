#FROM python
FROM python:3.11.10
RUN apt update &&\
    apt dist-upgrade -yq &&\
    apt install -yq cargo build-essential git libgeos-dev &&\
    apt autoremove -yq &&\
    apt autoclean -yq &&\
    useradd -m cadevil
WORKDIR /home/cadevil
ADD requirements.txt requirements.txt
ADD src src
ADD Makefile Makefile
USER cadevil
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt && make collectstatic
ENTRYPOINT make debug
