# Uso responsable

danterodia reúne información pública sobre personas, empresas e infraestructuras. Que un
dato sea público **no significa que puedas tratarlo como quieras**: quien investiga es el
responsable de lo que hace con él. Este documento resume el marco legal español y europeo
que suele aplicar, lo que la herramienta hace para ayudarte a cumplirlo y lo que sigue
dependiendo de ti.

> Esto es una guía práctica, **no asesoramiento jurídico**. Si vas a investigar a personas
> de forma profesional o con consecuencias para ellas, consulta con tu delegado de
> protección de datos o con un abogado.

## Antes de empezar: cinco preguntas

1. **¿Para qué?** Escribe una finalidad concreta («verificar que el administrador del
   proveedor X no tiene cargos incompatibles», no «investigar a Fulano»). Si no sabes
   escribirla, no empieces.
2. **¿Con qué base legal?** Normalmente, el interés legítimo (art. 6.1.f RGPD), que exige
   ponderar tu interés frente a los derechos de la persona y documentar esa ponderación.
3. **¿Es necesario?** Usa solo las fuentes que sirvan a esa finalidad (`-s`) y la
   profundidad mínima (`--depth 0` por defecto).
4. **¿Puedes hacerlo tú?** Investigar a personas **por encargo de un tercero** puede ser
   una actividad de investigación privada, reservada en España a detectives habilitados
   (Ley 5/2014, de Seguridad Privada).
5. **¿Cuánto tiempo lo guardarás?** Decide el plazo antes y borra el caso cuando acabe.

## Usos legítimos y usos que no lo son

| Normalmente legítimo | No lo es |
|---|---|
| *Due diligence* de proveedores, socios o inversiones (cargos en el BORME, contratos públicos, noticias). | Localizar, vigilar o seguir a una persona: puede ser acoso (art. 172 ter del Código Penal). |
| Auditar tu propia exposición o la de tu organización (dominios, emails, cuentas). | Buscar datos de una expareja, un vecino o alguien con quien tienes un conflicto. |
| Verificar noticias y desinformación. | Reunir datos para *doxing*, presionar o intimidar a alguien. |
| Periodismo de investigación, con su propio marco (art. 85 RGPD). | Acceder a cuentas ajenas o probar contraseñas (arts. 197 y 197 bis CP). |
| Ciberseguridad defensiva y ejercicios autorizados por escrito. | Phishing, suplantación o ingeniería social contra quien investigas. |
| Investigación académica con datos minimizados. | Perfilar a alguien por su salud, ideología, religión u orientación sexual. |

## Marco legal (resumen)

### Protección de datos: RGPD y LOPDGDD

- **Finalidad determinada** (art. 5.1.b RGPD). Por eso cada caso exige `--finalidad` y la
  guarda.
- **Minimización** (art. 5.1.c). Recoge solo lo que necesitas.
- **Exactitud** (art. 5.1.d). Un homónimo no es la persona: ver «Errores típicos».
- **Limitación del plazo de conservación** (art. 5.1.e). Borra los casos cuando ya no los
  necesites.
- **Base jurídica** (art. 6). El interés legítimo exige una ponderación documentada.
- **Categorías especiales** (art. 9): salud, ideología, religión, orientación sexual,
  origen étnico, datos biométricos… Su tratamiento está prohibido salvo excepciones
  concretas, como los datos que la propia persona ha hecho manifiestamente públicos
  (art. 9.2.e).
- **Datos penales** (art. 10 RGPD y art. 10 LOPDGDD): condenas, infracciones y medidas de
  seguridad. Un particular solo puede tratarlos en supuestos muy limitados.
- **Deber de informar** (art. 14). Si recoges datos de alguien sin obtenerlos de esa
  persona, en principio tienes que informarle. Hay excepciones (por ejemplo, cuando hacerlo
  es imposible o exige un esfuerzo desproporcionado, art. 14.5.b), pero tienes que poder
  justificarlas.
- **Derechos de las personas** (arts. 15-22): acceso, rectificación, supresión, oposición…
  Tienes que poder atenderlos; `caso ver` y `caso borrar` te ayudan.
- **Evaluación de impacto** (art. 35). Si vas a perfilar a personas de forma sistemática o
  a gran escala, probablemente la necesites.

### Código Penal

- **Art. 197**: descubrimiento y revelación de secretos.
- **Art. 197 bis**: acceso sin autorización a sistemas de información. Probar una contraseña
  en la cuenta de otra persona encaja aquí, aunque la contraseña sea `123456`.
- **Art. 197 ter**: producir o facilitar programas pensados para cometer los anteriores.
- **Art. 172 ter**: acoso, incluida la vigilancia insistente.

### Condiciones de uso de las plataformas

El scraping de redes sociales y buscadores suele ir contra sus condiciones de uso. En la UE
no es delito en sí mismo, pero supone un riesgo contractual y de bloqueo, y si hay datos
personales también un riesgo de RGPD. Por eso danterodia prioriza APIs oficiales y datos
abiertos.

## Lo que hace la herramienta por ti

| Medida | Cómo |
|---|---|
| Finalidad obligatoria | Cada caso la pide y la guarda; queda en el informe y en el log. |
| Fuentes sensibles desactivadas | `politicos_judicial` e `indultos` solo funcionan con `--sensibles`. Los edictos de TESTRA solo se importan con `indice locales --incluir-testra` y, aun así, solo aparecen con `--sensibles`. Nunca se pivota sobre entidades sensibles. |
| Sin categorías especiales innecesarias | De Wikidata no se extrae religión; la ideología solo con `--sensibles`. |
| DNI | La CLI no lo pide y no hay dorks con DNI. Si una integración lo pasa como atributo, solo se valida la letra de control y el hallazgo lo muestra anonimizado. En los datos que se extraen de las noticias se cuenta cuántos DNI o IBAN aparecen, sin copiarlos (la evidencia en bruto sí conserva la página completa). |
| Nada activo | No hay inicios de sesión, pruebas de contraseñas, formularios de reseteo, sondeo SMTP ni spoofing. Esas funciones del original no se han portado. |
| Homónimos | La confianza de las coincidencias solo por nombre baja según su frecuencia en España (INE), y se explica en el informe. |
| Plazo de conservación | `DANTERODIA_CASE_RETENTION_DAYS` (90 días por defecto) y `caso purgar`. |
| Supresión | `caso borrar` elimina el caso, sus evidencias y su log. |
| Trazabilidad | Cada dato lleva fuente, URL, fecha UTC y, si procede, evidencia con SHA-256. |
| Red identificable y comedida | User-Agent propio, ritmo limitado por dominio, caché para no repetir peticiones. No se falsea el User-Agent para saltarse bloqueos. |
| Secretos | Las claves de API van en `.env` o en variables de entorno, nunca en el código ni en los informes. |

## Lo que sigue dependiendo de ti

- **Elegir bien la finalidad y respetarla.** La herramienta la registra, pero no puede
  comprobar que sea cierta.
- **No activar `--sensibles` sin una base legal clara.** Recuerda: **imputado no es
  condenado**, y un indulto de hace veinte años rara vez es pertinente.
- **Verificar antes de concluir.** Un hallazgo es un indicio con una confianza, no un hecho.
- **Proteger los datos.** La carpeta de datos (`DANTERODIA_DATA_DIR`) contiene casos y
  evidencias con datos personales. Guárdala en un disco cifrado y no la sincronices con
  servicios que no controles.
- **No difundir los informes** más allá de quien los necesite para la finalidad declarada.
- **Borrar** cuando acabe la finalidad, sin esperar a la purga automática.
- **Respetar los índices locales.** Los datos del BOE, BORME o PLACSP son públicos para un
  fin (publicidad oficial, transparencia). Reutilizarlos para perfilar a particulares no
  está cubierto automáticamente por ese fin.

## Errores típicos

- **Confundir homónimos.** «María García López» la comparten miles de personas en España.
  Una coincidencia solo por nombre vale poco; busca un segundo dato independiente (CIF,
  cargo, localidad, fecha).
- **Pivotar demasiado.** Con `--depth 2` o `3` acabas recogiendo datos de personas que no
  tienen nada que ver con tu finalidad. Sube la profundidad solo si lo necesitas.
- **Tomar un perfil como prueba de identidad.** Que exista una cuenta con un nick no
  significa que sea de la persona que buscas.
- **Tratar una puntuación como un veredicto.** La puntuación de `verificar_noticia` explica
  señales; no sustituye a una verificación periodística.

## Datos de terceros incluidos

Los datasets empaquetados (INE, Civio, Wikipedia, WhatsMyName) tienen su propia licencia y
fecha de corte: ver [NOTICE](../NOTICE). Los de Wikipedia y WhatsMyName son CC BY-SA 4.0 y
exigen atribución si los redistribuyes.

## Más información

- Funciones del original que se retiraron y por qué: [AUDITORIA.md §7](AUDITORIA.md).
- Agencia Española de Protección de Datos: <https://www.aepd.es/>.
