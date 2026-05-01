# -*- coding: utf-8 -*-
"""
Epic Games Free Game Collection - Data Models

Defines the Pydantic data structures for Epic Games promotions, orders, and items.

@Time    : 2026/05/01
@Author  : akapzg
@GitHub  : https://github.com/akapzg/epic-gamer-gemini
"""

from typing import List

from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    description: str
    offerId: str
    namespace: str


class Order(BaseModel):
    orderType: str
    orderId: str
    items: List[OrderItem] = Field(default_factory=list)


class CompletedOrder(BaseModel):
    offerId: str
    namespace: str


class PromotionGame(BaseModel):
    title: str
    id: str
    namespace: str
    description: str
    offerType: str
    url: str
