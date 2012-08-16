Prerequisites
-------------

* Python 2.6
* pip

Dependencies
------------

    pip install django==1.4.1

    pip install docutils

    pip install lettuce

    pip install nose

Web application setup
---------------------

    python webapp/manage.py syncdb

    python webapp/manage.py runserver 0.0.0.0:8000

Testing
-------

    python webapp/manage.py harvest -d webapp

    lettuce whisper