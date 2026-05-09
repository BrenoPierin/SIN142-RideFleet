"""
Banco de dados em memória para a Semana 1.
Na Semana 2, isso será substituído por PostgreSQL + persistência real.
"""
from app.models.ride import Ride
from app.models.driver import Driver
from app.models.passenger import Passenger

# Simulação de banco em memória (dicts indexados por id)
rides: dict[str, Ride] = {}
drivers: dict[str, Driver] = {}
passengers: dict[str, Passenger] = {}
