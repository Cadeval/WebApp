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
ADD templates templates
ADD src src
ADD schema schema
ADD static static
ADD Makefile Makefile
RUN mkdir collected_static && chown -R cadevil:cadevil /home/cadevil
USER cadevil
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt && make collectstatic
ENTRYPOINT make debug
