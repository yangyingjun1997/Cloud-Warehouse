from django.test import SimpleTestCase

from apps.accounts.password_policy import is_exempt, validate_password_strength


class PasswordPolicyTests(SimpleTestCase):
    def test_service_accounts_are_exempt(self):
        for name in ('admin', 'label_print', 'Admin', 'LABEL_PRINT'):
            self.assertTrue(is_exempt(name), f'{name} 应为豁免账号')
        self.assertIsNone(validate_password_strength('1', 'admin'))
        self.assertIsNone(validate_password_strength('1', 'label_print'))

    def test_minimum_length(self):
        self.assertIsNotNone(validate_password_strength('Ab1!'))
        self.assertIsNone(validate_password_strength('Abc123!x', 'someone'))

    def test_common_weak_passwords_rejected(self):
        for pwd in ('password123', 'admin123', '12345678', 'qwerty123'):
            with self.subTest(pwd=pwd):
                self.assertIsNotNone(validate_password_strength(pwd, 'someone'))

    def test_pure_numeric_and_alpha_rejected(self):
        self.assertIsNotNone(validate_password_strength('123456789', 'someone'))
        self.assertIsNotNone(validate_password_strength('abcdefghij', 'someone'))

    def test_sequential_rejected(self):
        self.assertIsNotNone(validate_password_strength('abcdefgh', 'someone'))
        self.assertIsNotNone(validate_password_strength('87654321', 'someone'))

    def test_repeated_char_rejected(self):
        self.assertIsNotNone(validate_password_strength('aaaaaaaa', 'someone'))

    def test_password_containing_username_rejected(self):
        self.assertIsNotNone(validate_password_strength('zhangsan123', 'zhangsan'))

    def test_reasonable_password_accepted(self):
        for pwd in ('Wms2#2026x', 'Ware!house9', 'Xk7$mQp2'):
            with self.subTest(pwd=pwd):
                self.assertIsNone(validate_password_strength(pwd, 'someone'))
