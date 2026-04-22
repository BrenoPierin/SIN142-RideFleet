# RideFleet - Federação Distribuída de Serviços de Transporte

![GitHub License](https://img.shields.io/badge/license-MIT-blue)
![Course](https://img.shields.io/badge/SIN142-Sistemas%20Distribu%C3%ADdos-orange)
![University](https://img.shields.io/badge/UFV-Rio%20Parana%C3%ADba-red)

## 📌 Sobre o Projeto
O **RideFleet** é um ecossistema de transporte por aplicativo federado, desenvolvido como projeto prático para a disciplina **SIN 142 (Sistemas Distribuídos)** na **Universidade Federal de Viçosa (UFV)**.

Este repositório contém a implementação do **Serviço de Grupo [NOME DO SEU GRUPO OU LETRA]**. O objetivo é gerenciar solicitações de corridas de forma autônoma e, em casos de alta demanda ou indisponibilidade, delegar tarefas para outros serviços da federação através de protocolos distribuídos.

---

## 🛠️ Mecanismos de Sistemas Distribuídos Implementados
Conforme exigido na especificação, o projeto implementa os seguintes conceitos:

1. **Travas Distribuídas (Exclusão Mútua):** Garantia de que um motorista não aceite duas corridas simultâneas e que uma corrida não seja atribuída a dois motoristas.
2. **Transações Distribuídas (Saga/2PC):** Coordenação do fluxo de pagamento e reserva entre serviços, incluindo mecanismos de compensação em caso de falha.
3. **Algoritmo de Consenso (Leilão):** Protocolo para escolha determinística de qual parceiro da federação receberá uma corrida delegada.
4. **Resiliência (Circuit Breaker):** Isolamento de serviços parceiros que apresentem instabilidade, evitando o efeito cascata de falhas.
5. **Ordenação de Eventos (Relógios Lógicos):** Implementação de Relógios de Lamport ou Vetoriais para garantir a causalidade das operações no log do sistema.

---

## 🚀 Tecnologias Utilizadas
* **Linguagem:** [Ex: Go / Java / Python / Node.js]
* **Comunicação:** [Ex: gRPC / REST]
* **Mensageria/Coordenação:** [Ex: RabbitMQ / Redis / etcd]
* **Containerização:** Docker & Docker Compose
* **Observabilidade:** Prometheus & Grafana

---
