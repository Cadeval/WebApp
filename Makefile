# Minimal makefile
#
# instructs make to invoke a single instance of the shell and provide it with the entire recipe,
# regardless of how many lines it contains.
.ONESHELL:

install:
	cd src && \
	python -m pip install --upgrade pip && \
	pip install -r requirements.txt

debug:
	cd src && \
	python manage.py runserver 0.0.0.0:8000

run:
	cd src && \
	python -m uvicorn --workers 4 webapp.asgi:application --lifespan off --host 127.0.0.1 --port 8000

testrun:
	cd src && \
	python -m gunicorn webapp.asgi:application -k uvicorn.workers.UvicornWorker --reload --preload --threads 8 --reuse-port -b 127.0.0.1:8000

migrate:
	cd src && \
	python manage.py makemigrations
	python manage.py makemigrations model_manager
	python manage.py migrate --run-syncdb

test:
	cd src && \
	python manage.py test

flush:
	cd src && \
	python manage.py flush

superuser:
	cd src && \
	python manage.py createsuperuser

collectstatic:
	cd src && \
	python manage.py collectstatic