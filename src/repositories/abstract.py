from __future__ import annotations

import abc
from collections.abc import Sequence
from typing import Generic, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models import Base

AbstractModel = TypeVar("AbstractModel")


class Repository(Generic[AbstractModel]):
    """Repository abstract class."""

    type_model: type[Base]
    session: Session

    def __init__(self, type_model: type[Base], session: Session) -> None:
        """
        Initialize abstract repository class.

        :param type_model: Which model will be used for operations
        :param session: Session in which repository will work.
        """
        self.type_model = type_model
        self.session = session

    def get(self, ident: int | str) -> AbstractModel:
        """
        Get an ONE model from the database with PK.

        :param ident: Key which need to find entry in database
        :return:
        """
        return self.session.get(entity=self.type_model, ident=ident)  # type: ignore  # noqa: PGH003

    def get_by_where(self, whereclause) -> AbstractModel | None:  # noqa: ANN001
        """
        Get an ONE model from the database with whereclause.

        :param whereclause: Clause by which entry will be found
        :return: Model if only one model was found, else None.

        """
        statement = select(self.type_model).where(whereclause)
        return (self.session.execute(statement)).one_or_none()  # type: ignore  # noqa: PGH003

    def get_many(self, whereclause, limit: int = 100, order_by=None) -> Sequence[Base]:  # noqa: ANN001
        """
        Get many models from the database with whereclause.

        :param whereclause: Where clause for finding models
        :param limit: (Optional) Limit count of results
        :param order_by: (Optional) Order by clause.

        Example:
        >> Repository.get_many(Model.id == 1, limit=10, order_by=Model.id)

        :return: List of founded models

        """
        statement = select(self.type_model).filter(whereclause).limit(limit)
        if order_by:
            statement = statement.order_by(order_by)

        return (self.session.scalars(statement)).all()

    def delete(self, whereclause) -> None:  # noqa: ANN001
        """
        Delete model from the database.

        :param whereclause: (Optional) Which statement
        :return: Nothing
        """
        statement = delete(self.type_model).where(whereclause)
        self.session.execute(statement)

    @abc.abstractmethod
    def new(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        """
        Add new entry of model to the database.

        :return: Nothing.
        """
        ...
