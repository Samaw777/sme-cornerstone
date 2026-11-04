"""Vercel entry point: every request is routed here (see vercel.json) and handled by Django."""

from config.wsgi import application

app = application
