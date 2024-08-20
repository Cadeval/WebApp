# Minimal makefile
#

run:
	# gunicorn cadevil.asgi:application -k uvicorn.workers.UvicornWorker 127.0.0.1:8000
	# uvicorn --reload --workers 3 cadevil.asgi:application --lifespan off --host 127.0.0.1 --port 8000
	#python manage.py runserver_plus --threaded 127.0.0.1:8000
	 python manage.py runserver 127.0.0.1:8000

migrate:
	python manage.py makemigrations
	python manage.py migrate --run-syncdb

flush:
	python manage.py flush

superuser:
	python manage.py createsuperuser