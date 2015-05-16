# -*- coding: utf-8 -*-

import six
import toolz
import collections
from enum import Enum
from dateparser.date import DateDataParser

from . import schema


class AccountType(Enum):
    CHECKING = 'Checking'
    SAVINGS = 'Savings'
    CREDIT_CARD = 'CreditCard'
    CASH = 'Cash'
    LINE_OF_CREDIT = 'LineOfCredit'
    PAYPAL = 'Paypal'
    MERCHANT_ACCOUNT = 'MerchantAccount'
    INVESTMENT_ACCOUNT = 'InvestmentAccount'
    MORTGAGE = 'Mortgage'
    OTHER_ASSET = 'OtherAsset'
    OTHER_LIABILITY = 'OtherLiability'


class CategoryType(Enum):
    OUTFLOW = 'OUTFLOW'


class TransactionStatus(Enum):
    CLEARED = 'Cleared'
    RECONCILED = 'Reconciled'
    UNCLEARED = 'Uncleared'


class Model(object):
    _entity_type = None

    def __init__(self, ynab, entity):
        self._ynab = ynab
        self._entity = entity

    @classmethod
    @toolz.curry
    def _from_flat(cls, ynab, data):
        return cls(ynab, cls._entity_type.from_flat(data))

    @property
    def id(self):
        return self._entity.entityId

    @property
    def is_valid(self):
        return not self._entity.isTombstone


class Account(Model):
    _entity_type = schema.Account

    def __repr__(self):
        return '<Account: {}>'.format(self.name)

    @property
    def name(self):
        return self._entity.accountName

    @property
    def type(self):
        return AccountType(self._entity.accountType)

    @property
    def on_budget(self):
        return self._entity.onBudget

    @property
    def last_reconciled_date(self):
        return self._entity.lastReconciledDate

    @property
    def last_reconciled_balance(self):
        return self._entity.lastReconciledBalance

    @property
    def last_reconciled_check_number(self):
        return self._entity.lastReconciledCheckNumber

    @property
    def hidden(self):
        return self._entity.hidden

    @property
    def payees(self):
        return self._ynab.payees.filter('target_account', self)

    @property
    def transactions(self):
        return self._ynab.transactions.filter('account', self)

    @property
    def inbound_transactions(self):
        return self._ynab.transactions.filter('target_account', self)

    @property
    def balance(self):
        return round(sum(self.transactions.amount), 3)

    @property
    def cleared_balance(self):
        return round(sum(self.transactions.filter('cleared', True).amount), 3)


class Payee(Model):
    _entity_type = schema.Payee

    def __repr__(self):
        return '<Payee: {}>'.format(self.name)

    @property
    def name(self):
        return self._entity.name

    @property
    def target_account(self):
        return self._ynab.accounts.by_id(self._entity.targetAccountId)

    @property
    def enabled(self):
        return self._entity.enabled

    @property
    def transactions(self):
        return self._ynab.transactions.filter('payee', self)


class CategoryModel(Model):
    @property
    def name(self):
        return self._entity.name

    @property
    def type(self):
        return CategoryType(self._entity.type)


class Category(CategoryModel):
    _entity_type = schema.SubCategory

    def __repr__(self):
        return '<Category: {}>'.format(self.full_name)

    @property
    def cached_balance(self):
        return self._entity.cachedBalance

    @property
    def master_category(self):
        return self._ynab.master_categories.by_id(self._entity.masterCategoryId)

    @property
    def has_unresolved_conflicts(self):
        return not self._entity.isResolvedConflict

    @property
    def note(self):
        return self._entity.note

    @property
    def full_name(self):
        return '{}/{}'.format(self.master_category.name, self.name)


class MasterCategory(CategoryModel):
    _entity_type = schema.MasterCategory

    def __init__(self, ynab, entity):
        super(MasterCategory, self).__init__(ynab, entity)
        self._categories = Categories(
            Category(ynab, category) for category in self._entity.subCategories or [])

    def __repr__(self):
        return '<MasterCategory: {}>'.format(self.name)

    @property
    def categories(self):
        return self._categories

    def __iter__(self):
        return iter(self._categories)


class TransactionModel(Model):
    @property
    def memo(self):
        return self._entity.memo

    @property
    def amount(self):
        return round(float(self._entity.amount or 0.), 3)

    @property
    def category(self):
        return self._ynab.categories.by_id(self._entity.categoryId)

    @property
    def target_account(self):
        return self._ynab.accounts.by_id(self._entity.targetAccountId)

    @property
    def transfer_transaction(self):
        return self._ynab.transactions.by_id(self._entity.transferTransactionId)


class SubTransaction(TransactionModel):
    _entity_type = schema.SubTransaction

    def __repr__(self):
        return '<SubTransaction: {:.2f} ({})>'.format(
            self.amount, self.category.name if self.category else 'no category')

    @property
    def parent(self):
        return self._ynab.transactions.by_id(self._entity.parentTransactionId)


class Transaction(TransactionModel):
    _entity_type = schema.Transaction

    def __init__(self, ynab, entity):
        super(Transaction, self).__init__(ynab, entity)
        self._sub_transactions = SubTransactions(
            SubTransaction(ynab, t) for t in self._entity.subTransactions or [])

    def __repr__(self):
        info = ''
        if self.category:
            info += ' ({})'.format(self.category.name)
        if self.payee:
            info += ' [{}]'.format(self.payee.name)
        return '<Transaction: [{:%d/%m/%y}]: {}: {:.2f}{}>'.format(
            self.date or 'no date', self.account.name if self.account else 'no account',
            self.amount, info)

    @property
    def date(self):
        return self._entity.date

    @property
    def status(self):
        return TransactionStatus(self._entity.cleared)

    @property
    def cleared(self):
        return self.status in (TransactionStatus.CLEARED, TransactionStatus.RECONCILED)

    @property
    def reconciled(self):
        return self.status == TransactionStatus.RECONCILED

    @property
    def accepted(self):
        return self._entity.accepted

    @property
    def account(self):
        return self._ynab.accounts.by_id(self._entity.accountId)

    @property
    def payee(self):
        return self._ynab.payees.by_id(self._entity.payeeId)

    @property
    def date_entered_from_schedule(self):
        return self._entity.dateEnteredFromSchedule

    @property
    def sub_transactions(self):
        return self._sub_transactions


class ModelCollection(collections.Sequence):
    _model_type = None
    _index_key = None

    def __init__(self, elements):
        self._elements = list(e for e in elements if e.is_valid)
        self._index = {element.id: element for element in self._elements}

    @classmethod
    @toolz.curry
    def _from_flat(cls, ynab, data):
        return cls(map(cls._model_type._from_flat(ynab), data))

    def __len__(self):
        return len(self._elements)

    def __getitem__(self, key):
        if isinstance(key, six.string_types):
            if self._index_key is not None:
                for element in self:
                    if getattr(element, self._index_key) == key:
                        return element
            raise KeyError(key)
        else:
            return self._elements[key]

    def __getattr__(self, key):
        return [getattr(element, key) for element in self]

    def __repr__(self):
        return repr(self._elements)

    def __str__(self):
        return str(self._elements)

    def by_id(self, id):
        return self._index.get(id, None)

    def sort_by(self, field):
        self._elements = sorted(self._elements, key=lambda element: getattr(element, field))

    def filter(self, field, value):
        return type(self)(element for element in self if getattr(element, field) == value)


class Accounts(ModelCollection):
    _model_type = Account
    _index_key = 'name'


class Payees(ModelCollection):
    _model_type = Payee
    _index_key = 'name'


class MasterCategories(ModelCollection):
    _model_type = MasterCategory
    _index_key = 'name'


class Categories(ModelCollection):
    _model_type = Category
    _index_key = 'full_name'


class Transactions(ModelCollection):
    _model_type = Transaction

    def between(self, start=None, end=None):
        parser = DateDataParser()
        transactions = list(self)
        if start is not None:
            start = parser.get_date_data(start)['date_obj'].date()
            transactions = [t for t in transactions if t.date >= start]
        if end is not None:
            end = parser.get_date_data(end)['date_obj'].date()
            transactions = [t for t in transactions if t.end <= end]
        return type(self)(transactions)

    def since(self, date):
        return self.between(start=date)

    def till(self, date):
        return self.between(end=date)


class SubTransactions(ModelCollection):
    _model_type = SubTransaction
