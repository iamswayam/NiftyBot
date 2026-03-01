"""
Kotak Neo Kill Switch — browser automation via Selenium
Triggered automatically after trade closes.
"""
import time
import logging
log = logging.getLogger("KillSwitch")

# Import CONFIG from main bot
try:
    from trading_bot import CONFIG
except ImportError:
    CONFIG = {}


class KillSwitch:
    def trigger_web_killswitch(self):
        print("\n  🔴 Opening browser to activate Kill Switch on Kotak Neo...")
        print("  ⚠️  Remember: This only blocks app/web orders.")
        print("  ✅  Bot has already stopped — API orders are blocked too.\n")
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            import pyotp

            options = webdriver.ChromeOptions()
            # options.add_argument("--headless")  # Uncomment to run invisible
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=options
            )
            wait = WebDriverWait(driver, 30)

            driver.get("https://neo.kotaksecurities.com/")
            time.sleep(3)

            try:
                # Enter mobile number
                user = wait.until(EC.presence_of_element_located(
                    (By.XPATH, "//input[contains(@placeholder,'Mobile') or contains(@placeholder,'User')]")
                ))
                user.clear()
                user.send_keys(CONFIG.get("MOBILE_NUMBER", "").replace("+91", ""))

                pwd = driver.find_element(By.XPATH, "//input[@type='password']")
                pwd.send_keys(CONFIG.get("PASSWORD", ""))

                btn = driver.find_element(By.XPATH, "//button[contains(text(),'Login') or contains(text(),'CONTINUE')]")
                btn.click()
                time.sleep(3)

                # TOTP
                totp_secret = CONFIG.get("TOTP_SECRET", "")
                if totp_secret:
                    totp = pyotp.TOTP(totp_secret)
                    otp_field = wait.until(EC.presence_of_element_located(
                        (By.XPATH, "//input[contains(@placeholder,'OTP') or contains(@placeholder,'TOTP')]")
                    ))
                    otp_field.send_keys(totp.now())
                    verify = driver.find_element(By.XPATH, "//button[contains(text(),'Verify') or contains(text(),'Continue')]")
                    verify.click()
                    time.sleep(3)

                # MPIN if prompted
                try:
                    mpin = wait.until(EC.presence_of_element_located(
                        (By.XPATH, "//input[contains(@placeholder,'MPIN')]")
                    ))
                    mpin.send_keys(CONFIG.get("MPIN", ""))
                    driver.find_element(By.XPATH, "//button[contains(text(),'Login')]").click()
                    time.sleep(4)
                except Exception:
                    pass

            except Exception as e:
                log.warning(f"Login step: {e}")

            # Go to Kill Switch page
            driver.get("https://neo.kotaksecurities.com/profile/account-details")
            time.sleep(3)

            try:
                ks = wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//*[contains(text(),'Kill Switch')]")
                ))
                ks.click()
                time.sleep(2)

                manage = wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(text(),'Manage')]")
                ))
                manage.click()
                time.sleep(2)

                # Try to select F&O specifically
                try:
                    fo = driver.find_element(By.XPATH,
                        "//*[contains(text(),'F&O') or contains(text(),'Derivatives')]//ancestor::label | "
                        "//input[@value='FO'] | //input[@value='NFO']"
                    )
                    fo.click()
                    time.sleep(1)
                except Exception:
                    pass

                disable = wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(text(),'Disable')]")
                ))
                disable.click()
                time.sleep(2)

                try:
                    confirm = wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "//button[contains(text(),'Confirm') or contains(text(),'Yes')]")
                    ))
                    confirm.click()
                    time.sleep(2)
                    log.info("✅ Kill Switch activated on app/web!")
                    print("  ✅ Kill Switch activated on Kotak Neo app/web!")
                except Exception:
                    pass

            except Exception as e:
                log.error(f"Kill Switch click failed: {e}")
                self._manual_instructions()

            driver.quit()

        except ImportError:
            print("  ⚠️  Selenium not installed: pip install selenium webdriver-manager")
            self._manual_instructions()
        except Exception as e:
            log.error(f"Kill Switch error: {e}")
            self._manual_instructions()

    def _manual_instructions(self):
        print("\n  ⚠️  Please activate Kill Switch MANUALLY:")
        print("  Neo App → Profile → Account Details → Segments → Kill Switch → Manage → Disable F&O")
        print("  OR visit: https://neo.kotaksecurities.com/profile/account-details\n")
